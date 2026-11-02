import threading
import urllib.error
import urllib.request

import pytest

from agent.android_portal import PortalServer, adb_serial, input_text_arg


class Android:
    def __init__(self):
        self.calls = []

    def screen(self):
        return b"PNG"

    def tap(self, x, y):
        self.calls.append(("tap", x, y))

    def text(self, value):
        self.calls.append(("text", value))

    def key(self, name):
        self.calls.append(("key", name))

    def open_app(self, name, store=False):
        self.calls.append(("app", name, store))


@pytest.fixture
def portal():
    android = Android()
    server = PortalServer(("127.0.0.1", 0), android, "secret")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield android, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def post(url, body=b""):
    return urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST"))


def test_wrong_token_is_rejected_everywhere(portal):
    _, base = portal
    for path in ("/bad/", "/bad/screen.png"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + path)
    with pytest.raises(urllib.error.HTTPError):
        post(base + "/bad/tap?x=1&y=2")


def test_page_and_screen_are_served_with_token(portal):
    _, base = portal
    assert b"<img" in urllib.request.urlopen(base + "/secret/").read()
    assert urllib.request.urlopen(base + "/secret/screen.png").read() == b"PNG"


def test_tap_is_forwarded(portal):
    android, base = portal
    post(base + "/secret/tap?x=10&y=20")
    assert android.calls == [("tap", 10, 20)]


def test_bad_tap_is_rejected(portal):
    android, base = portal
    for query in ("x=a&y=2", "x=-1&y=2", "x=1"):
        with pytest.raises(urllib.error.HTTPError):
            post(base + "/secret/tap?" + query)
    assert android.calls == []


def test_text_is_forwarded(portal):
    android, base = portal
    post(base + "/secret/text", "pass word!".encode())
    assert android.calls == [("text", "pass word!")]


def test_only_known_keys_are_allowed(portal):
    android, base = portal
    post(base + "/secret/key?name=BACK")
    with pytest.raises(urllib.error.HTTPError):
        post(base + "/secret/key?name=POWER")
    assert android.calls == [("key", "BACK")]


def test_text_argument_is_safe_for_adb_shell():
    assert input_text_arg("a b") == "a%sb"
    assert input_text_arg("x@y.com!") == "'x@y.com!'"
    assert input_text_arg("it's") == "'it'\"'\"'s'"
    assert input_text_arg("$(reboot)") == "'$(reboot)'"


def test_only_allowed_apps_can_be_opened(portal):
    android, base = portal
    post(base + "/secret/app?name=kakao&store=1")
    assert android.calls == [("app", "kakao", True)]
    with pytest.raises(urllib.error.HTTPError):
        post(base + "/secret/app?name=com.evil.app")


def test_configured_adb_serial_does_not_need_waydroid(monkeypatch):
    from agent import android_portal
    calls = []
    monkeypatch.setenv("ANDROID_SERIAL", "127.0.0.1:15555")
    monkeypatch.setattr(android_portal.subprocess, "run", lambda command, **kwargs: calls.append(command))
    assert adb_serial() == "127.0.0.1:15555"
    assert calls == [["adb", "connect", "127.0.0.1:15555"]]
