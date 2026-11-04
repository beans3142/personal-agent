"""Keeps one headless browser running so quick web tasks skip the browser start-up.

Playwright's sync API must stay on the thread that started it, so one worker thread owns the
browser and runs every job. Each job should open and close its own context.
"""

from __future__ import annotations

import queue
import threading
import os
import time

MAX_USES = 50
JOB_TIMEOUT_SECONDS = 150
_job_state = threading.local()


def job_cancelled() -> bool:
    """Whether this worker-thread job's caller expired; no forced interruption."""
    event = getattr(_job_state, 'cancelled', None)
    deadline = getattr(_job_state, 'deadline', None)
    return bool(event and (event.is_set() or time.monotonic() >= deadline))


class ManagedBrowser:
    def __init__(self, browser, playwright, external=False):
        self._browser, self._playwright = browser, playwright
        self._external, self._closed = external, False

    def __getattr__(self, name):
        return getattr(self._browser, name)

    def is_connected(self):
        return not self._closed and self._browser.is_connected()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            # External Chrome belongs to its daemon. Stop our driver/connection,
            # never send browser.close() to the shared daemon.
            if not self._external and self._browser.is_connected():
                self._browser.close()
        finally:
            self._playwright.stop()


def launch_chromium():
    endpoint = os.environ.get('DAEVIS_BROWSER_CDP', '')
    if endpoint and endpoint != 'http://127.0.0.1:9222':
        raise ValueError('CDP endpoint must be http://127.0.0.1:9222')
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = (playwright.chromium.connect_over_cdp(endpoint) if endpoint
                   else playwright.chromium.launch(headless=True))
        return ManagedBrowser(browser, playwright, external=bool(endpoint))
    except Exception:
        playwright.stop()
        raise


class BrowserWorker:
    def __init__(self, launch=launch_chromium, max_uses: int = MAX_USES):
        self.launch = launch
        self.max_uses = max_uses
        self.jobs: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        browser, uses = None, 0
        while True:
            job = self.jobs.get()
            if job is None:
                break
            function, done, cancelled, deadline = job
            if cancelled.is_set() or time.monotonic() >= deadline:
                cancelled.set()
                continue
            _job_state.cancelled, _job_state.deadline = cancelled, deadline
            try:
                if browser is None or not browser.is_connected() or uses >= self.max_uses:
                    previous, browser = browser, None
                    if previous is not None:
                        previous.close()
                    browser, uses = self.launch(), 0
                if job_cancelled():
                    continue
                uses += 1
                done.put((True, function(browser)))
            except Exception as error:
                done.put((False, error))
            finally:
                _job_state.cancelled = _job_state.deadline = None
        if browser is not None:
            browser.close()

    def run(self, function, timeout: float = JOB_TIMEOUT_SECONDS):
        done: queue.Queue = queue.Queue(maxsize=1)
        cancelled = threading.Event()
        self.jobs.put((function, done, cancelled, time.monotonic() + timeout))
        try:
            ok, value = done.get(timeout=timeout)
        except queue.Empty:
            cancelled.set()
            raise
        if not ok:
            raise value
        return value

    def stop(self) -> None:
        self.jobs.put(None)
        self.thread.join(timeout=10)


_shared: BrowserWorker | None = None
_shared_lock = threading.Lock()


def shared_browser() -> BrowserWorker:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = BrowserWorker()
        return _shared
