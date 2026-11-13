"""Owner's Google Calendar and Drive through an Apps Script web app in their account.

The script (deploy/google_bridge.gs) runs as the owner; every call carries a
shared key. No model is involved in the calls themselves.
"""
import base64
from datetime import date, datetime, timedelta, timezone
import json
import mimetypes
from pathlib import Path
import urllib.request

from .loop import Tool

KST = timezone(timedelta(hours=9))
MAX_DRIVE_BYTES = 30 * 1024 * 1024  # Apps Script request limit is ~50MB after base64.
URL_FILE = Path('data/google-bridge.url')


class GoogleBridgeError(RuntimeError):
    pass


def kst_iso(value):
    """ISO date or datetime; values without an offset are Korean time."""
    text = str(value or '').strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f'날짜는 ISO 형식이어야 해요: {text!r}')
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    return moment.isoformat()


def _kst_text(value):
    moment = datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(KST)
    return moment.strftime('%Y-%m-%d %H:%M')


def _event_view(event):
    view = {'title': event.get('title'), 'start_kst': _kst_text(event['start']), 'end_kst': _kst_text(event['end']),
            'all_day': bool(event.get('all_day'))}
    if event.get('location'):
        view['location'] = event['location']
    return view


class GoogleBridge:
    def __init__(self, url, key, timeout=120):
        self.url, self.key, self.timeout = url, key, timeout

    def post(self, body):
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), method='POST',
                                         headers={'Content-Type': 'application/json'})
        # Apps Script answers with a redirect; urllib follows it as GET, which is what it expects.
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read(1_000_000))

    def call(self, action, **fields):
        reply = self.post({'key': self.key, 'action': action, **fields})
        if not reply.get('ok'):
            raise GoogleBridgeError(reply.get('error') or 'google bridge failed')
        return reply

    def rest(self, method, url, headers, body):
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status, dict(response.headers), response.read(1_000_000)

    def drive_session(self, name, size, mime):
        """Create the Drive file now and return a resumable upload address for its content.

        The PC streams the file straight to that address; the owner token stays on the server.
        """
        grant = self.call('drive_token')
        auth = {'Authorization': 'Bearer ' + grant['token']}
        _, _, body = self.rest('POST', 'https://www.googleapis.com/drive/v3/files?fields=id,name,webViewLink',
                               {**auth, 'Content-Type': 'application/json'},
                               json.dumps({'name': name, 'parents': [grant['folder']], 'mimeType': mime}).encode())
        created = json.loads(body)
        _, headers, _ = self.rest('PATCH', f'https://www.googleapis.com/upload/drive/v3/files/{created["id"]}?uploadType=resumable',
                                  {**auth, 'Content-Type': 'application/json; charset=UTF-8',
                                   'X-Upload-Content-Type': mime, 'X-Upload-Content-Length': str(size)}, b'{}')
        session = headers.get('Location') or headers.get('location')
        if not session:
            raise GoogleBridgeError('드라이브 업로드 주소를 받지 못했어요')
        return {'id': created['id'], 'url': created['webViewLink'], 'session': session}

    def upload(self, path):
        path = Path(path)
        size = path.stat().st_size
        if size > MAX_DRIVE_BYTES:
            raise GoogleBridgeError(f'드라이브 업로드는 {MAX_DRIVE_BYTES // (1024 * 1024)}MB까지예요 ({size // (1024 * 1024)}MB)')
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        return self.call('drive_upload', name=path.name, mime=mime,
                         data=base64.b64encode(path.read_bytes()).decode())['file']


def from_env(env, url_file=URL_FILE):
    key = env.get('DAEVIS_GOOGLE_KEY')
    url = env.get('DAEVIS_GOOGLE_BRIDGE_URL')
    if not url and Path(url_file).is_file():
        url = Path(url_file).read_text(encoding='utf-8').strip()
    if not key or not url:
        return None
    return GoogleBridge(url, key)


def build_google_tools(bridge):
    text = {'type': 'string'}

    def guard(run):
        def wrapped(args):
            try:
                return run(args)
            except (ValueError, GoogleBridgeError) as error:
                return f'error: {error}'
            except Exception as error:
                return f'error: 구글 연결 실패 ({type(error).__name__}); 결과가 불확실하니 같은 요청을 바로 반복하지 마세요'
        return wrapped

    def list_events(args):
        start = kst_iso(args['start'])
        end = kst_iso(args['end']) if args.get('end') else (datetime.fromisoformat(start) + timedelta(days=1)).isoformat()
        events = bridge.call('calendar_list', start=start, end=end)['events']
        return json.dumps({'events': [_event_view(e) for e in events]}, ensure_ascii=False)

    def add_event(args):
        title = str(args.get('title') or '').strip()
        if not title:
            raise ValueError('일정 제목이 필요해요')
        all_day = bool(args.get('all_day'))
        start = kst_iso(args.get('start'))
        if all_day:
            start = datetime.combine(datetime.fromisoformat(start).date(), datetime.min.time(), KST).isoformat()
            end = start
        else:
            end = kst_iso(args['end']) if args.get('end') else (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
            if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
                raise ValueError('끝나는 시각이 시작보다 늦어야 해요')
        event = bridge.call('calendar_add', title=title, start=start, end=end, all_day=all_day,
                            location=str(args.get('location') or ''), description=str(args.get('description') or ''))['event']
        return json.dumps({'created': _event_view(event)}, ensure_ascii=False)

    return [
        Tool('calendar_list', '소유자 구글 캘린더(기본 캘린더) 일정 조회. start/end는 ISO 날짜나 시각(시간대 없으면 한국 시각). end 생략 시 하루.',
             {'type': 'object', 'properties': {'start': text, 'end': text}, 'required': ['start'], 'additionalProperties': False},
             guard(list_events)),
        Tool('calendar_add', '소유자 구글 캘린더에 일정 하나를 만든다. start/end는 ISO(예: 2026-11-15T15:00, 한국 시각). '
                             'end 생략 시 1시간. 종일 일정은 all_day=true와 날짜만. 만든 결과(created)를 답에 그대로 알린다. 같은 일정을 다시 만들지 않는다.',
             {'type': 'object', 'properties': {'title': text, 'start': text, 'end': text, 'all_day': {'type': 'boolean'},
                                               'location': text, 'description': text},
              'required': ['title', 'start'], 'additionalProperties': False},
             guard(add_event)),
    ]
