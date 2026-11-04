from agent.login_portal import SESSION_MARKERS, is_logged_in, tunnel_url_from


class Context:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return [{"name": name, "value": "x"} for name in self._cookies]


def test_every_portal_site_has_a_login_cookie():
    assert set(SESSION_MARKERS) == {"naver", "kakao", "google", "discord"}


def test_logged_in_when_marker_cookie_exists():
    assert is_logged_in(Context(["NID_AUT", "other"]), "naver") is True


def test_stale_cookie_does_not_close_naver_login_portal():
    from types import SimpleNamespace
    context = Context(['NID_AUT'])
    context.pages = [SimpleNamespace(url='https://nid.naver.com/nidlogin.login')]
    assert is_logged_in(context, 'naver') is False


def test_not_logged_in_without_marker_cookie():
    assert is_logged_in(Context(["NID_JKL"]), "naver") is False


def test_tunnel_url_is_found_in_log_line():
    line = "INF |  https://calm-river-12.trycloudflare.com  |"
    assert tunnel_url_from(line) == "https://calm-river-12.trycloudflare.com"


def test_tunnel_url_ignores_other_https_links():
    assert tunnel_url_from("see https://developers.cloudflare.com/docs") is None
