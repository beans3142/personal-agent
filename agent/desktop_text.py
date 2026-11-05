"""General Chat UI lane; no credentials, tools or conversation history forwarded."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time


@contextmanager
def ui_lock():
    # One shared UI: login-image assistance and general questions must serialize.
    import fcntl
    path=Path(__file__).resolve().parent.parent/'data'/'desktop-chat-ui.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:
        os.chmod(path,0o600)
        try:
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('desktop UI busy') from None
        try:yield
        finally:fcntl.flock(f.fileno(),fcntl.LOCK_UN)


class DesktopText:
    name='ChatGPT Chat'

    def __init__(self,run=None): self.run=run

    def ask(self,prompt):
        if not isinstance(prompt,str) or not 1<=len(prompt)<=12000:
            raise ValueError('text prompt size invalid')
        from .hermes_runner import run_process
        env={key:os.environ[key] for key in ('PATH','HOME','DISPLAY','LANG','LC_ALL','PLAYWRIGHT_BROWSERS_PATH') if key in os.environ}
        result=(self.run or run_process)([sys.executable,'-m','agent.desktop_text','--ask'],
            input=json.dumps({'prompt':prompt}),text=True,encoding='utf-8',env=env,
            timeout=85,start_new_session=True,cwd=str(Path(__file__).resolve().parent.parent))
        if result.returncode:raise ValueError('desktop Chat unavailable')
        value=json.loads(result.stdout).get('text')
        if not isinstance(value,str) or not value.strip():raise ValueError('empty desktop reply')
        return value.strip()


def ask_ui(prompt):
    from playwright.sync_api import sync_playwright
    import re
    with ui_lock(),sync_playwright() as p:
        browser=p.chromium.connect_over_cdp('http://127.0.0.1:9350',timeout=10000)
        try:
            pages=[page for c in browser.contexts for page in c.pages
                   if page.get_by_role('textbox',name='Ask ChatGPT',exact=True).count()]
            if len(pages)!=1:raise ValueError('desktop Chat login required')
            page=pages[0]
            show=page.get_by_role('button',name='Show sidebar',exact=True)
            if show.count():show.click()
            page.get_by_role('button',name='New chat',exact=True).filter(has_text=re.compile(r'^\s*New chat\s*$')).click()
            page.get_by_role('button',name='Chat',exact=True).click()
            reply=page.locator('[data-markdown-text-style="assistant-message"]:visible')
            if reply.count():reply.first.wait_for(state='hidden',timeout=10000)
            page.get_by_role('textbox',name='Ask ChatGPT',exact=True).fill(prompt)
            page.get_by_role('button',name='Send',exact=True).click()
            deadline,last,stable=time.monotonic()+60,'',0
            while time.monotonic()<deadline:
                page.wait_for_timeout(600)
                text=reply.last.inner_text() if reply.count() else ''
                stable=stable+1 if text and text==last else 0;last=text
                generating=page.get_by_role('button',name=re.compile('Stop (generating|streaming)',re.I)).count()
                if stable>=4 and not generating:return text
            raise TimeoutError('desktop Chat timed out; no automatic resend')
        finally:browser.close()


if __name__=='__main__':
    try:
        if sys.argv[1:]!=['--ask']:raise ValueError('unsupported invocation')
        data=json.load(sys.stdin)
        if set(data)!={'prompt'} or not isinstance(data['prompt'],str) or not 1<=len(data['prompt'])<=12000:
            raise ValueError('invalid prompt')
        print(json.dumps({'text':ask_ui(data['prompt'])},ensure_ascii=False))
    except Exception as error:
        print(type(error).__name__,file=sys.stderr);raise SystemExit(1)
