"""MCP server that lets other agents send requests to Daevis through this PC."""
import asyncio
import json

from desktop import mcp_server


class FakeRelay:
    def __init__(self, states=()):
        self.states, self.submitted = list(states), []

    def submit(self, text, via):
        self.submitted.append((text, via))
        return 'j1'

    def status(self, job_id):
        return self.states.pop(0) if self.states else {'status': 'dispatched', 'result': {'step': '⏳ 검색하는 중…', 'steps': ['웹 검색하는 중']}}

    def follow(self, job_id, on_update, sleep=None, limit=320):
        state = None
        for _ in range(limit):
            state = self.status(job_id)
            on_update(state)
            if state['status'] not in ('queued', 'dispatched'):
                return state
        return {'status': 'timeout', 'result': None}

    def history(self, before=None, limit=20):
        return {'items': [{'id': 'j1', 'text': '안녕', 'reply': '네', 'status': 'succeeded', 'created': 1.0}], 'more': False, 'me': '데스크톱'}


def test_ask_waits_and_returns_the_answer_with_steps():
    r = FakeRelay([{'status': 'dispatched', 'result': {'step': 's', 'steps': ['웹 검색하는 중']}},
                   {'status': 'succeeded', 'result': {'reply': '맑아요', 'text': '**맑아요**'}}])
    result = mcp_server.ask(r, '날씨', wait=True)
    assert result == {'id': 'j1', 'status': 'succeeded', 'answer': '**맑아요**', 'spoken': '맑아요', 'steps': ['웹 검색하는 중']}
    assert r.submitted == [('날씨', 'typed')]


def test_ask_without_waiting_returns_an_id_to_poll():
    r = FakeRelay()
    assert mcp_server.ask(r, '긴 작업', wait=False) == {'id': 'j1', 'status': 'queued'}
    state = mcp_server.request_status(r, 'j1')
    assert state['status'] == 'dispatched' and state['current_step'] == '⏳ 검색하는 중…' and state['steps'] == ['웹 검색하는 중']


def test_payment_questions_are_reported_but_not_answerable():
    r = FakeRelay([{'status': 'dispatched', 'result': {'step': 's', 'approval': {'id': 'abcdefabcdefabcd', 'text': '결제 확인', 'expires_in': 90}}}])
    state = mcp_server.request_status(r, 'j1')
    assert state['payment_question'] == '결제 확인' and 'Discord' in state['note']


def test_ask_rejects_empty_text():
    r = FakeRelay()
    result = mcp_server.ask(r, '  ', wait=True)
    assert result['status'] == 'rejected' and r.submitted == []


def test_history_is_trimmed_for_agents():
    page = mcp_server.history(FakeRelay(), limit=5)
    assert page == {'items': [{'id': 'j1', 'text': '안녕', 'answer': '네', 'status': 'succeeded', 'created': 1.0}], 'more': False}


def test_server_lists_three_tools_with_descriptions():
    server = mcp_server.build(FakeRelay())
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {'ask', 'request_status', 'history'}
    assert all(t.description for t in tools)


def test_tool_call_through_the_server():
    r = FakeRelay([{'status': 'succeeded', 'result': {'reply': '네'}}])
    server = mcp_server.build(r)
    content = asyncio.run(server.call_tool('ask', {'text': '안녕'}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert json.loads(blocks[0].text)['answer'] == '네'
