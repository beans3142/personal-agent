"""Generate an image with the ChatGPT desktop app on the server (owner's subscription).

The image lands in the request's outbox as a PNG, so the gateway attaches it to
the reply in whatever Discord channel or DM the request came from; KakaoTalk
delivery is a separate tool that takes the saved file.
"""
import base64
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

from .loop import Tool

TIMEOUT = 200


def request_text(prompt):
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError('image prompt size invalid')
    return '다음 내용으로 이미지를 생성해 줘. 설명 없이 이미지 한 장만 만들어 줘.\n' + prompt.strip()


def save_png(data, folder):
    from PIL import Image
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise ValueError('not an image') from None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = 'image-' + uuid.uuid4().hex[:10] + '.png'
    if data.startswith(b'\x89PNG'):
        (folder / name).write_bytes(data)
    else:
        image.convert('RGBA' if 'A' in image.getbands() else 'RGB').save(folder / name, 'PNG')
    return name


def generate_ui(text):
    """Drive the logged-in ChatGPT desktop app over CDP; returns raw image bytes."""
    from playwright.sync_api import sync_playwright
    from .desktop_text import ui_lock
    with ui_lock(), sync_playwright() as p:
        browser = p.chromium.connect_over_cdp('http://127.0.0.1:9350', timeout=10000)
        try:
            pages = [page for c in browser.contexts for page in c.pages
                     if page.get_by_role('textbox', name='Ask ChatGPT', exact=True).count()]
            if len(pages) != 1:
                raise ValueError('desktop Chat login required')
            page = pages[0]
            show = page.get_by_role('button', name='Show sidebar', exact=True)
            if show.count():
                show.click()
            page.get_by_role('button', name='New chat', exact=True).filter(has_text=re.compile(r'^\s*New chat\s*$')).click()
            page.get_by_role('button', name='Chat', exact=True).click()
            images = page.locator('img[alt^="Generated image"]')
            before = images.count()
            page.get_by_role('textbox', name='Ask ChatGPT', exact=True).fill(text)
            page.get_by_role('button', name='Send', exact=True).click()
            deadline, last, stable = time.monotonic() + TIMEOUT - 20, None, 0
            while time.monotonic() < deadline:
                page.wait_for_timeout(2000)
                if images.count() <= before:
                    continue
                current = images.last.evaluate('e => [e.src, e.naturalWidth]')
                generating = page.get_by_role('button', name=re.compile('Stop (generating|streaming)', re.I)).count()
                # The picture is drawn progressively; take it once it stops changing.
                stable = stable + 1 if current == last and current[1] >= 256 and not generating else 0
                last = current
                if stable >= 2:
                    encoded = images.last.evaluate(
                        'async e => { const r = await fetch(e.src); const b = new Uint8Array(await r.arrayBuffer());'
                        ' let s = ""; for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode(...b.subarray(i, i + 32768));'
                        ' return btoa(s); }')
                    return base64.b64decode(encoded)
            raise TimeoutError('image was not generated in time; no automatic retry')
        finally:
            browser.close()


def build_image_tools(run=None):
    def generate(args):
        from .artifacts import current_outbox
        from .hermes_runner import run_process
        text = request_text(args['prompt'])
        env = {key: os.environ[key] for key in ('PATH', 'HOME', 'DISPLAY', 'LANG', 'LC_ALL', 'PLAYWRIGHT_BROWSERS_PATH') if key in os.environ}
        try:
            result = (run or run_process)([sys.executable, '-m', 'agent.chat_image', '--image'], input=json.dumps({'prompt': text}),
                                          text=True, encoding='utf-8', env=env, timeout=TIMEOUT, start_new_session=True,
                                          cwd=str(Path(__file__).resolve().parent.parent))
            if result.returncode:
                raise ValueError('ChatGPT image generation failed')
            data = base64.b64decode(json.loads(result.stdout)['image'])
            name = save_png(data, current_outbox(Path(os.environ.get('AGENT_OUTBOX', 'data/outbox'))))
        except Exception as error:
            return json.dumps({'status': 'failed', 'reason': type(error).__name__}, ensure_ascii=False)
        return json.dumps({'status': 'generated', 'file': name,
                           'note': 'The image is attached to this reply automatically. Use kakao_send_image with this file to send it on KakaoTalk.'},
                          ensure_ascii=False)

    schema = {'type': 'object', 'required': ['prompt'], 'additionalProperties': False,
              'properties': {'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 2000}}}
    return [Tool('image_generate', 'ChatGPT(소유자 구독)로 이미지 한 장을 만든다. 30초~3분 걸린다. 만든 이미지는 이 요청의 답과 함께 '
                 '요청한 Discord 채널/DM에 자동으로 첨부된다. 카톡으로 보내려면 결과의 file을 kakao_send_image에 넘긴다.', schema, generate)]


if __name__ == '__main__':
    try:
        if sys.argv[1:] != ['--image']:
            raise ValueError('unsupported invocation')
        data = json.load(sys.stdin)
        if set(data) != {'prompt'} or not isinstance(data['prompt'], str):
            raise ValueError('invalid prompt')
        print(json.dumps({'image': base64.b64encode(generate_ui(data['prompt'])).decode()}))
    except Exception as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
