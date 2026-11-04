import threading
import queue
from types import SimpleNamespace

import pytest

from agent.browser_worker import BrowserWorker


class FakeBrowser:
    def __init__(self):
        self.closed = False
        self.thread = threading.get_ident()

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True


def make_worker(**kwargs):
    made = []

    def launch():
        browser = FakeBrowser()
        made.append(browser)
        return browser

    return BrowserWorker(launch=launch, **kwargs), made


def test_browser_is_reused_and_used_on_its_own_thread():
    worker, made = make_worker()
    try:
        threads = {worker.run(lambda b: threading.get_ident()) for _ in range(3)}
        assert len(made) == 1
        assert threads == {made[0].thread}
        assert threading.get_ident() not in threads
    finally:
        worker.stop()


def test_errors_reach_the_caller_and_worker_keeps_working():
    worker, _ = make_worker()
    try:
        with pytest.raises(ValueError):
            worker.run(lambda b: (_ for _ in ()).throw(ValueError("boom")))
        assert worker.run(lambda b: "ok") == "ok"
    finally:
        worker.stop()


def test_disconnected_browser_is_relaunched():
    worker, made = make_worker()
    try:
        worker.run(lambda b: b.close())
        worker.run(lambda b: None)
        assert len(made) == 2
    finally:
        worker.stop()


def test_browser_is_recycled_after_use_limit():
    worker, made = make_worker(max_uses=2)
    try:
        for _ in range(3):
            worker.run(lambda b: None)
        assert len(made) == 2
        assert made[0].closed
    finally:
        worker.stop()


def test_calls_from_many_threads_are_serialized():
    worker, made = make_worker()
    results = []
    try:
        threads = [threading.Thread(target=lambda: results.append(worker.run(lambda b: 1))) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert results == [1] * 5
        assert len(made) == 1
    finally:
        worker.stop()


def test_launch_and_recycling_stop_every_playwright_owner(monkeypatch):
    from agent.browser_worker import launch_chromium
    drivers = []
    def start():
        driver = SimpleNamespace(stopped=0, browser=FakeBrowser())
        driver.chromium = SimpleNamespace(launch=lambda **kw: driver.browser)
        driver.stop = lambda: setattr(driver, 'stopped', driver.stopped + 1)
        drivers.append(driver)
        return driver
    monkeypatch.delenv('DAEVIS_BROWSER_CDP', raising=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: SimpleNamespace(start=start))
    worker = BrowserWorker(launch=launch_chromium, max_uses=1)
    try:
        worker.run(lambda b: None)
        worker.run(lambda b: None)
        assert drivers[0].stopped == 1
    finally:
        worker.stop()
    assert [driver.stopped for driver in drivers] == [1, 1]


def test_disconnected_browser_still_stops_its_driver(monkeypatch):
    from agent.browser_worker import launch_chromium
    drivers = []
    def start():
        browser = FakeBrowser()
        driver = SimpleNamespace(stopped=False, browser=browser, chromium=SimpleNamespace(launch=lambda **kw: browser))
        driver.stop = lambda: setattr(driver, 'stopped', True)
        drivers.append(driver)
        return driver
    monkeypatch.delenv('DAEVIS_BROWSER_CDP', raising=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: SimpleNamespace(start=start))
    worker = BrowserWorker(launch=launch_chromium)
    try:
        worker.run(lambda b: drivers[-1].browser.close())
        worker.run(lambda b: None)
        assert drivers[0].stopped
    finally:
        worker.stop()


@pytest.mark.parametrize('endpoint', ['http://example.com:9222', 'http://localhost:9222',
                                      'http://127.0.0.1:9222/path', 'http://127.0.0.1:9222?x',
                                      'http://127.0.0.1:9333', 'https://127.0.0.1:9222'])
def test_cdp_rejects_arbitrary_endpoints(monkeypatch, endpoint):
    from agent.browser_worker import launch_chromium
    monkeypatch.setenv('DAEVIS_BROWSER_CDP', endpoint)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: pytest.fail('invalid CDP reached driver startup'))
    with pytest.raises(ValueError, match='CDP'):
        launch_chromium()


def test_cdp_disconnect_stops_driver_without_closing_external_browser(monkeypatch):
    from agent.browser_worker import launch_chromium
    browser, connected, stopped = FakeBrowser(), [], []
    driver = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url: connected.append(url) or browser),
                             stop=lambda: stopped.append(True))
    monkeypatch.setenv('DAEVIS_BROWSER_CDP', 'http://127.0.0.1:9222')
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: SimpleNamespace(start=lambda: driver))
    managed = launch_chromium()
    managed.close()
    managed.close()
    assert connected == ['http://127.0.0.1:9222']
    assert stopped == [True] and not browser.closed


def test_launch_failure_stops_driver(monkeypatch):
    from agent.browser_worker import launch_chromium
    stopped = []
    def fail(**kwargs):
        raise RuntimeError('launch failed')
    driver = SimpleNamespace(chromium=SimpleNamespace(launch=fail), stop=lambda: stopped.append(True))
    monkeypatch.delenv('DAEVIS_BROWSER_CDP', raising=False)
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: SimpleNamespace(start=lambda: driver))
    with pytest.raises(RuntimeError):
        launch_chromium()
    assert stopped == [True]


def test_timed_out_queued_work_is_skipped_and_running_work_observes_cancellation():
    from agent.browser_worker import job_cancelled
    worker, _ = make_worker()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    observed, queued_effects, errors = [], [], []
    def running(browser):
        started.set()
        release.wait(2)
        observed.append(job_cancelled())
        finished.set()
    def first_caller():
        try:
            worker.run(running, timeout=0.05)
        except queue.Empty:
            errors.append('timeout')
    caller = threading.Thread(target=first_caller)
    caller.start()
    try:
        assert started.wait(1)
        with pytest.raises(queue.Empty):
            worker.run(lambda b: queued_effects.append('ran'), timeout=0.02)
        caller.join(1)
        release.set()
        assert finished.wait(1)
        assert worker.run(lambda b: job_cancelled()) is False
        assert observed == [True] and not queued_effects and errors == ['timeout']
        assert job_cancelled() is False
    finally:
        release.set()
        caller.join(1)
        worker.stop()


@pytest.mark.parametrize('cancel_at', ['before_screenshot', 'after_screenshot'])
def test_transit_cancel_checks_suppress_or_remove_only_own_screenshots(monkeypatch, tmp_path, cancel_at):
    from datetime import datetime, timezone
    from pathlib import Path
    from agent import browser_worker, transit
    cancelled, screenshots, closed = [], [], []
    sentinel = tmp_path / 'other-request.png'
    sentinel.write_bytes(b'keep')
    class Page:
        url = 'https://map.naver.com/'
        @property
        def first(self): return self
        def locator(self, *args, **kwargs): return self
        def get_by_role(self, *args, **kwargs): return self
        def nth(self, *args): return self
        def goto(self, *args, **kwargs): pass
        def click(self, *args, **kwargs): pass
        def go_back(self, *args, **kwargs): pass
        def inner_text(self): return 'route'
        def all_inner_texts(self): return ['route']
        def count(self): return 0
        def wait_for(self, **kwargs):
            if kwargs.get('state') == 'hidden' and cancel_at == 'before_screenshot':
                cancelled.append(True)
        def screenshot(self, path, **kwargs):
            screenshots.append(path)
            Path(path).write_bytes(b'private screenshot')
            if cancel_at == 'after_screenshot':
                cancelled.append(True)
        def close(self): closed.append(True)
    monkeypatch.setattr(browser_worker, 'job_cancelled', lambda: bool(cancelled))
    monkeypatch.setattr(transit, '_place', lambda *args: 'place')
    monkeypatch.setattr(transit, '_departure', lambda *args: None)
    monkeypatch.setattr(transit, '_sort', lambda *args: ['route'])
    monkeypatch.setattr(transit, 'choose_candidates', lambda *args: [{'summary': 'route'}])
    monkeypatch.setattr(transit, 'candidate_key', lambda text: text)
    monkeypatch.setattr(transit, 'compact_detail', lambda text: text)
    with pytest.raises(TimeoutError, match='expired'):
        transit._search_in(SimpleNamespace(new_page=lambda **kwargs: Page()), 'a', 'b',
                           datetime.now(timezone.utc), tmp_path)
    assert len(screenshots) == (1 if cancel_at == 'after_screenshot' else 0)
    assert list(tmp_path.glob('*.png')) == [sentinel]
    assert closed == [True]
