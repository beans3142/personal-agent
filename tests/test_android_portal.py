import threading
import urllib.error
import urllib.request

import pytest

from agent.android_portal import PortalServer, quoted_text


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


def test_text_quoting_survives_shell_characters():
    assert quoted_text("a b") == "'a%sb'"
    assert quoted_text("it's$") == "'it'\\''s$'"
