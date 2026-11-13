"""Owner's phone alarm at an exact time, set by the Galaxy Daevis app.

The main agent queues one `phone_alarm_at` job for the phone node through the relay.
The app picks it up (on open, or its 15-minute background check) and registers an
exact one-shot alarm; the job waits on the relay until the alarm time.
"""
from datetime import datetime, timedelta, timezone
import json
import time
import uuid

from .loop import Tool

KST = timezone(timedelta(hours=9))
HORIZON = 31 * 86400


def build_phone_alarm_tools(post, phone_node, now=time.time, issuer='agent-main'):
    def run(args):
        try:
            moment = datetime.fromisoformat(str(args.get('at', '')).strip())
        except ValueError:
            return 'error: at은 ISO 시각이어야 해요 (예: 2026-11-15T04:00)'
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=KST)
        at = int(moment.timestamp())
        if not now() < at <= now() + HORIZON:
            return 'error: 알람은 지금 이후 31일 안으로만 맞출 수 있어요'
        text = str(args.get('text') or '').strip()[:60] or '대비스 알람'
        try:
            job = post('/request', {'node': issuer, 'target': phone_node, 'action': 'phone_alarm_at',
                                    'payload': {'at': at, 'text': text}, 'request_key': 'alarm-' + uuid.uuid4().hex,
                                    'ttl_seconds': 300})
        except Exception as error:
            return f'error: 폰 알람 요청 실패 ({type(error).__name__})'
        return json.dumps({'queued': job.get('status') == 'queued', 'alarm_kst': moment.astimezone(KST).strftime('%Y-%m-%d %H:%M'),
                           'text': text, 'note': '폰 대비스 앱이 받으면(앱을 열거나 15분 안팎) 그 시각에 한 번 울린다. '
                                                 '아직 폰에 등록된 것은 아니라고 알린다.'}, ensure_ascii=False)

    return [Tool('phone_alarm', '소유자 폰(Galaxy 대비스 앱)에 정해진 시각에 한 번 울리는 알람을 맞춘다. '
                                'at은 ISO 시각(시간대 없으면 한국 시각, 예: 2026-11-15T04:00), text는 알람 이름. '
                                '"폰 알람", "알람 맞춰줘", "깨워줘"에 쓴다. Discord 알림 예약(schedule_add)과 다르다.',
                 {'type': 'object', 'properties': {'at': {'type': 'string'}, 'text': {'type': 'string'}},
                  'required': ['at'], 'additionalProperties': False}, run)]
