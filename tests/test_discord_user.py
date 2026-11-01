import pytest

from agent.discord_user import channel_url, pick_named


def test_pick_ignores_spaces_and_case():
    items = [{"name": "여기서채팅쳐주세요", "id": "1"}, {"name": "테스트", "id": "2"}]
    assert pick_named(items, "여기서 채팅쳐주세요")["id"] == "1"


def test_pick_rejects_ambiguous_or_missing_name():
    items = [{"name": "테스트1"}, {"name": "테스트2"}]
    with pytest.raises(RuntimeError):
        pick_named(items, "테스트")
    with pytest.raises(RuntimeError):
        pick_named(items, "없음")


def test_channel_url_uses_text_channels_only():
    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    def get(url, headers, timeout):
        if url.endswith("/users/@me/guilds"):
            return Response([{"name": "황-전", "id": "9"}])
        return Response([{"name": "방", "id": "1", "type": 2}, {"name": "방", "id": "2", "type": 0}])

    assert channel_url("t", "황-전", "방", get) == "https://discord.com/channels/9/2"
