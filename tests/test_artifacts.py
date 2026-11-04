import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.artifacts import outbox_scope, current_outbox
from agent.gateway import Gateway


def test_late_previous_request_image_is_not_sent_to_a_new_channel(tmp_path,monkeypatch):
    monkeypatch.setattr('agent.gateway.DATA',tmp_path)
    old=tmp_path/'outbox'/'old-request';old.mkdir(parents=True);(old/'private.png').write_bytes(b'old')
    new=tmp_path/'outbox'/'new-request';new.mkdir();(new/'route.png').write_bytes(b'new')
    channel=SimpleNamespace(send=AsyncMock())
    with outbox_scope(new):
        asyncio.run(Gateway.send_outbox(None,channel))
    assert channel.send.await_count==1
    assert channel.send.await_args.kwargs['file'].filename=='route.png'
    assert (old/'private.png').exists()


def test_scope_resets_and_defaults_do_not_cross_requests(tmp_path):
    default=tmp_path/'default'
    assert current_outbox(default)==default
    with outbox_scope(tmp_path/'one'):
        assert current_outbox(default)==tmp_path/'one'
    assert current_outbox(default)==default
