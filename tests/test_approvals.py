import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.approvals import ChannelApproval, PAYMENT, owner_policy, pending_requests, answer, payment_text, screen_amounts


LEASE = {"OWNER_ID": "1", "DAEVIS_MCP_OWNER_REQUEST": "1", "DAEVIS_MCP_REQUEST_EXPIRES": "9999999999"}


@pytest.mark.parametrize("label", ["결제하기", "32,900원 결제", "주문하기", "구매하기", "Pay now", "Place order"])
def test_payment_labels_are_detected(label):
    assert PAYMENT.search(label)


@pytest.mark.parametrize("label", ["삭제", "동의", "모두 허용", "로그아웃", "검색", "전송", "장바구니 담기"])
def test_other_buttons_are_not_payment(label):
    assert not PAYMENT.search(label)


def test_screen_amounts_lists_unique_prices_only():
    nodes = [{"text": "총 결제금액", "description": ""}, {"text": "32,900원", "description": ""},
             {"text": "", "description": "₩ 3,000"}, {"text": "32,900원", "description": ""},
             {"text": "수량 2", "description": ""}, {"text": "10000 KRW", "description": ""}]
    assert screen_amounts(nodes) == ["32,900원", "₩ 3,000", "10000 KRW"]


def test_owner_request_runs_everything_but_payment_without_asking():
    asked = []
    approve = owner_policy(LEASE, lambda name, args: asked.append(name) or False)
    for name in ["mail_send", "kakao_send", "n8n_run", "android_submit", "memory_forget", "android_click"]:
        assert approve(name, {"text": "x"})
    assert asked == []


def test_payment_asks_and_follows_owner_answer():
    assert owner_policy(LEASE, lambda *_: True)("payment", {"button": "결제하기"})
    assert not owner_policy(LEASE, lambda *_: False)("payment", {"button": "결제하기"})


def test_without_owner_lease_nothing_runs_and_nobody_is_asked():
    asked = []
    approve = owner_policy({"OWNER_ID": "1"}, lambda *a: asked.append(a) or True)
    assert not approve("mail_send", {})
    assert not approve("payment", {"button": "결제하기"})
    assert asked == []


def test_lease_expiring_during_payment_question_denies():
    env = dict(LEASE)
    def ask(*_):
        env["DAEVIS_MCP_REQUEST_EXPIRES"] = "0"
        return True
    assert not owner_policy(env, ask)("payment", {"button": "결제하기"})


def run_approval(tmp_path, reply, timeout=5):
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
        for request in pending_requests(tmp_path):
            if reply is not None:
                answer(tmp_path, request["id"], reply)
    approval = ChannelApproval(tmp_path, timeout=timeout, sleep=sleep, clock=lambda: now[0])
    return approval("payment", {"button": "결제하기", "package": "com.example.shop", "amounts": ["32,900원"]})


def test_channel_approval_allows_only_on_allow_answer(tmp_path):
    assert run_approval(tmp_path, "allow") is True
    assert run_approval(tmp_path, "deny") is False


def test_channel_approval_times_out_and_cleans_up(tmp_path):
    assert run_approval(tmp_path, None, timeout=3) is False
    assert pending_requests(tmp_path) == []
    assert not any((tmp_path / "approvals").glob("*"))


def test_request_file_carries_what_the_owner_needs(tmp_path):
    seen = []
    now = [0.0]
    def sleep(seconds):
        now[0] += seconds
        seen.extend(pending_requests(tmp_path))
    ChannelApproval(tmp_path, timeout=1, sleep=sleep, clock=lambda: now[0])("payment", {"button": "결제하기", "package": "com.example.shop", "amounts": ["32,900원"]})
    assert seen[0]["button"] == "결제하기"
    assert seen[0]["amounts"] == ["32,900원"]
    assert seen[0]["package"] == "com.example.shop"
    assert seen[0]["expires_in"] == 1


def test_missing_outbox_denies_without_waiting():
    assert ChannelApproval(None)("payment", {"button": "결제하기"}) is False


def test_answer_rejects_unknown_values_and_ids(tmp_path):
    with pytest.raises(ValueError):
        answer(tmp_path, "abc", "maybe")
    with pytest.raises(ValueError):
        answer(tmp_path, "../x", "allow")


def test_payment_text_is_readable_and_has_no_raw_json():
    text = payment_text({"button": "결제하기", "package": "com.coupang.mobile", "amounts": ["32,900원", "₩ 3,000"], "expires_in": 110})
    assert "결제하기" in text and "32,900원" in text and "쿠팡" in text
    assert "{" not in text and "110" not in text
    assert "1분 50초" in text
