"""MCP server (stdio) so other agents can ask Daevis through this PC's registration.

  Daevis.exe mcp                      (release exe)
  python -m desktop.mcp_server        (source)
  claude mcp add daevis -- "%USERPROFILE%\\.daevis\\app\\Daevis.exe" mcp

Tools: ask, request_status, history. Payment questions are reported but can only
be answered by the owner in the app window or on Discord.
"""
import json
from pathlib import Path
import sys

FINAL = ('succeeded', 'failed', 'expired', 'uncertain', 'cancelled', 'unknown', 'timeout')


def _summary(job_id, state):
    result = state.get('result') or {}
    summary = {'id': job_id, 'status': state.get('status')}
    if state.get('status') in FINAL:
        summary['answer'] = result.get('text') or result.get('reply') or ''
        if result.get('reply'):
            summary['spoken'] = result['reply']
    else:
        summary['current_step'] = result.get('step') or result.get('say') or '대기 중'
    if result.get('approval'):
        summary['payment_question'] = result['approval'].get('text', '')
        summary['note'] = '결제 승인은 소유자가 데스크톱 앱이나 Discord 버튼으로만 답할 수 있습니다.'
    return summary


def ask(relay, text, wait=True):
    text = (text or '').strip()
    if not 1 <= len(text) <= 500:
        return {'status': 'rejected', 'error': '요청은 1~500자여야 합니다.'}
    job_id = relay.submit(text, 'typed')
    if not wait:
        return {'id': job_id, 'status': 'queued'}
    seen = {'steps': []}
    def update(state):
        steps = (state.get('result') or {}).get('steps')
        if steps:
            seen['steps'] = steps
    final = relay.follow(job_id, update)
    return {**_summary(job_id, final), 'steps': seen['steps']}


def request_status(relay, job_id):
    state = relay.status(job_id)
    return {**_summary(job_id, state), 'steps': (state.get('result') or {}).get('steps', [])}


def history(relay, limit=10, before=None):
    page = relay.history(before=before, limit=max(1, min(50, int(limit))))
    items = [{'id': i.get('id'), 'text': i.get('text'), 'answer': i.get('reply', ''), 'status': i.get('status'),
              'created': i.get('created')} for i in page.get('items', [])]
    return {'items': items, 'more': bool(page.get('more'))}


def build(relay):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP('daevis')

    @server.tool(name='ask')
    def ask_tool(text: str, wait: bool = True) -> str:
        """Ask Daevis, the owner's personal assistant (KakaoTalk, mail, schedule, Baemin, web, PC/phone control).
        Write the request in Korean as the owner would say it. wait=true blocks until the answer (up to ~5 min,
        the main server may need 1-3 min to boot); wait=false returns an id for request_status."""
        return json.dumps(ask(relay, text, wait), ensure_ascii=False)

    @server.tool(name='request_status')
    def status_tool(id: str) -> str:
        """Current step, steps taken so far and the final answer of a request started with ask(wait=false)."""
        return json.dumps(request_status(relay, id), ensure_ascii=False)

    @server.tool(name='history')
    def history_tool(limit: int = 10, before: float | None = None) -> str:
        """Recent requests and answers from the owner's registered PCs, newest first. Use the last item's
        created value as before to page back."""
        return json.dumps(history(relay, limit, before), ensure_ascii=False)

    return server


def main():
    if __package__ in (None, ''):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from desktop import relay as relay_module
    config = relay_module.find_config()
    if not config:
        print('This PC is not registered with Daevis; open the app once to register.', file=sys.stderr)
        raise SystemExit(2)
    client = relay_module.Relay(json.loads(config.read_text(encoding='utf-8-sig')))
    build(client).run('stdio')


if __name__ == '__main__':
    main()
