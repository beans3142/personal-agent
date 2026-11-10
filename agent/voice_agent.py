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


async def voice_turn(gateway, text):
    from .artifacts import async_outbox_scope
    dm = await gateway.owner_dm()
    if hasattr(gateway, 'last_activity'):
        from datetime import datetime, timezone
        gateway.last_activity = datetime.now(timezone.utc)
    root = Path(tempfile.gettempdir()) / 'daevis-voice' / uuid.uuid4().hex
    async with gateway.turn_lock, async_outbox_scope(root) as outbox:
        approvals = asyncio.create_task(gateway.watch_approvals(dm, outbox))
        try:
            # Spoken requests always start a new conversation topic.
            reply = await asyncio.to_thread(gateway.turn, str(dm.id), '[[topic:new]] ' + text, gateway.approver(dm))
        except Exception as error:
            log.warning('voice turn failed (%s)', type(error).__name__)
            reply = str(error) if type(error).__name__ == 'HermesError' else '처리 중 오류가 났어요.'
        finally:
            approvals.cancel()
        await gateway.send_long(dm, '🎤 ' + text + '\n\n' + reply)
        await gateway.send_outbox(dm)
    return {'reply': spoken(reply) or '처리했어요.'}


async def poll_once(post, answer, node):
    job = (await asyncio.to_thread(post, '/poll', {'node': node}))['job']
    if job is None:
        return False
    status, result = 'failed', {'reply': '지원하지 않는 요청이에요.'}
    if job.get('action') == 'voice_command' and job.get('expires', 0) > time.time():
        try:
            result, status = await answer(job['payload']['text']), 'succeeded'
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
        with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
            return json.loads(response.read(65536))
    return post


async def agent_loop(gateway, base, token, node='agent-main'):
    post = relay_post(base, token)
    while True:
        try:
            await poll_once(post, lambda text: voice_turn(gateway, text), node)
            await asyncio.sleep(2)
        except Exception as error:
            log.warning('voice relay unavailable (%s)', type(error).__name__)
            await asyncio.sleep(30)
