"""Main-agent side of the PC voice assistant.

The main gateway polls the power relay as node 'agent-main'. Each voice job is
answered like an owner DM request; the full answer goes to the DM and a short
spoken version goes back to the relay.
"""
import asyncio
import json
import logging
from pathlib import Path
import tempfile
import time
import urllib.request
import uuid

from .speech import spoken

log = logging.getLogger(__name__)


REPORT_SECONDS = 1.5
RESULT_LIMIT = 2048  # Relay receipts hold at most this many JSON characters.


def receipt(reply):
    """Short spoken answer plus as much of the full answer as fits on the relay receipt."""
    result = {'reply': spoken(reply) or '처리했어요.', 'text': reply or ''}
    while len(json.dumps(result, ensure_ascii=False)) > RESULT_LIMIT:
        result['text'] = result['text'][:len(result['text']) - max(50, len(json.dumps(result, ensure_ascii=False)) - RESULT_LIMIT) - 1] + '…'
    return result


def current_state(outbox):
    """What the desktop app shows while the request runs: the step and an open payment question."""
    from .approvals import payment_text, pending_requests
    from .progress import LABELS, read_steps, render
    steps = read_steps(outbox)
    labels = []
    for step in steps:  # What the agent has done so far, one entry per change of activity.
        label = LABELS.get(step.get('tool'), '작업하는 중')
        if not labels or labels[-1] != label:
            labels.append(label)
    state = {'step': render(steps), 'steps': labels[-8:]}
    for request in pending_requests(outbox):
        state['approval'] = {'id': request.get('id'), 'text': payment_text(request), 'expires_in': request.get('expires_in', 0)}
        break
    return state


async def report_loop(outbox, report):
    from .approvals import answer
    while True:
        await asyncio.sleep(REPORT_SECONDS)
        try:
            answers = await asyncio.to_thread(report, await asyncio.to_thread(current_state, outbox))
            for item in answers or []:
                try:
                    await asyncio.to_thread(answer, outbox, item.get('approval'), item.get('value'))
                except ValueError:
                    pass
        except Exception as error:
            log.warning('progress report failed (%s)', type(error).__name__)


async def voice_turn(gateway, text, report=None, via='voice', topic=None, share=None):
    from .artifacts import async_outbox_scope
    dm = await gateway.owner_dm()
    if hasattr(gateway, 'last_activity'):
        from datetime import datetime, timezone
        gateway.last_activity = datetime.now(timezone.utc)
    root = Path(tempfile.gettempdir()) / 'daevis-voice' / uuid.uuid4().hex
    mark = '⌨️ ' if via == 'typed' else '🎤 '
    async with gateway.turn_lock, async_outbox_scope(root) as outbox:
        approvals = asyncio.create_task(gateway.watch_approvals(dm, outbox))
        reporter = asyncio.create_task(report_loop(outbox, report)) if report else None
        try:
            # A new request starts a conversation topic; a follow-up from the app continues its thread's topic.
            prefix = '[[topic:%s]] ' % topic if topic else '[[topic:new]] '
            reply = await asyncio.to_thread(gateway.turn, str(dm.id), prefix + text, gateway.approver(dm))
        except Exception as error:
            log.warning('voice turn failed (%s)', type(error).__name__)
            reply = str(error) if type(error).__name__ == 'HermesError' else '처리 중 오류가 났어요.'
        finally:
            approvals.cancel()
            if reporter:
                reporter.cancel()
        await gateway.send_long(dm, mark + text + '\n\n' + reply)
        links = await share_files(outbox, share) if share else []
        await gateway.send_outbox(dm)
    result = receipt(reply)
    last = getattr(gateway, '_last_topic', None)
    if isinstance(last, dict) and last.get('id'):
        result['topic'] = last['id']  # Lets the next follow-up in this thread continue the same topic.
        result = receipt_fit(result)
    return with_links(result, links) if links else result


async def share_files(outbox, share):
    """Download links for this request's files, so the desktop app can offer them too (Discord gets attachments)."""
    folder, links = Path(outbox) / 'files', []
    for path in sorted(folder.iterdir())[:5] if folder.is_dir() else []:
        try:
            links.append((path.name, await asyncio.to_thread(share, path)))
        except Exception as error:
            log.warning('file link failed (%s)', type(error).__name__)
    return links


def with_links(result, links):
    """Links go at the end of the answer text and survive trimming; the app shows them as download buttons."""
    block = ''.join(f'\n\n📎 {name}: {url}' for name, url in links)
    body = result['text']
    while body and len(json.dumps({**result, 'text': body + block}, ensure_ascii=False)) > RESULT_LIMIT:
        body = body[:max(0, len(body) - 80)].rstrip('…') + '…'
    return {**result, 'text': body + block}


def relay_share(base, token):
    """Upload a file for the main agent itself and return a day-long download link for the owner."""
    from .pc_tools import relay_link
    link = relay_link(base, token)

    def share(path):
        meta = link.upload('agent-main', path.name, Path(path).read_bytes())
        return link.share(meta['id'])['url']
    return share


def receipt_fit(result):
    while len(json.dumps(result, ensure_ascii=False)) > RESULT_LIMIT:
        result['text'] = result['text'][:len(result['text']) - 60] + '…'
    return result


async def poll_once(post, answer, node, wait=0):
    endpoint, body = ('/agent/poll', {'node': node, 'wait': wait}) if wait else ('/poll', {'node': node})
    job = (await asyncio.to_thread(post, endpoint, body))['job']
    if job is None:
        return False
    status, result = 'failed', {'reply': '지원하지 않는 요청이에요.'}
    if job.get('action') == 'voice_command' and job.get('expires', 0) > time.time():
        def report(state):
            return post('/progress', {'node': node, 'id': job['id'], 'result': state}).get('answers', [])
        try:
            payload = job['payload']
            result, status = await answer(payload['text'], payload.get('via', 'voice'), report, payload.get('topic')), 'succeeded'
        except Exception:
            log.exception('voice answer failed')
            result = {'reply': '처리 중 오류가 났어요.'}
    await asyncio.to_thread(post, '/ack', {'node': node, 'id': job['id'], 'status': status, 'result': result})
    return True


def relay_post(base, token):
    def post(endpoint, body):
        request = urllib.request.Request(base.rstrip('/') + endpoint, data=json.dumps(body).encode(), method='POST',
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        with urllib.request.build_opener(NoRedirect).open(request, timeout=35) as response:
            return json.loads(response.read(65536))
    return post


async def agent_loop(gateway, base, token, node='agent-main'):
    post = relay_post(base, token)
    share = relay_share(base, token)
    while True:
        try:
            await poll_once(post, lambda text, via, report, topic=None: voice_turn(gateway, text, report, via, topic, share), node, wait=20)
        except Exception as error:
            log.warning('voice relay unavailable (%s)', type(error).__name__)
            await asyncio.sleep(30)
