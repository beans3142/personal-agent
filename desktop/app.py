"""Daevis desktop: a HUD window for typed and spoken ("대비스, ...") requests.

Run from the repository: python -m desktop.app   (the release exe runs the same code)
The page is desktop/ui/index.html shown in WebView2; desktop/bridge.py is its only API.
"""
import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # PyInstaller runs this file as a script.

from desktop import bridge, core, relay


def page():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / 'ui' / 'index.html' if (base / 'ui').exists() else base / 'desktop' / 'ui' / 'index.html'


def coalesce(events):
    """Microphone levels arrive ten times a second; only the newest one is worth drawing."""
    levels = [e for e in events if e['kind'] == 'level']
    return [e for e in events if e['kind'] != 'level'] + levels[-1:]


class Desktop:
    def __init__(self):
        self.events = queue.Queue()
        self.api = bridge.Bridge(emit=self.events.put, autostart=self.autostart)
        self.api._on_register = self.start
        self.window = None

    @staticmethod
    def autostart(enabled):
        if getattr(sys, 'frozen', False):
            core.set_autostart(enabled)

    def start(self, config_path):
        config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
        client = relay.Relay(config)
        speaker = self.api

        class Voice:  # Controller speaks through the bridge so the HUD shows the speaking state.
            def speak(self, text, voice, rate):
                speaker._say(text, voice, rate)
        controller = core.Controller(client, Voice(), self.api._settings, self.events.put)
        self.api._connect(client, controller)
        threading.Thread(target=self.background, args=(config_path, controller), daemon=True).start()

    def background(self, config_path, controller):
        try:
            core.retire_legacy_voice()
        except Exception:
            pass
        try:
            core.start_power_receiver(config_path)
        except Exception:
            self.events.put({'kind': 'status', 'text': 'PC 전원 수신기를 시작하지 못했어요', 'level': 'warn'})
        try:
            self.autostart(self.api._settings['autostart'])
        except OSError:
            pass
        # Voice lives in the headless agent so it keeps listening after this window closes.
        from desktop import agent
        if not agent.running():
            try:
                core.start_agent()
            except OSError:
                core.Listener(lambda text: controller.request(text, 'voice'),
                              lambda text: self.events.put({'kind': 'status', 'text': text}),
                              lambda: self.api._settings['listen'],
                              on_event=self.events.put).start()
                return
        self.events.put({'kind': 'status', 'text': '백그라운드에서 "대비스"를 듣고 있어요'})

    def pump(self):
        """Forward Python events to the page in order, once the page is ready for them."""
        log = (Path.home() / '.daevis' / 'desktop' / 'app.log').open('a', encoding='utf-8', buffering=1)
        self.api._ready.wait()
        while True:
            batch = [self.events.get()]
            time.sleep(0.05)
            while not self.events.empty():
                batch.append(self.events.get_nowait())
            for event in batch:
                if event['kind'] == 'status':
                    self.api._status = event['text']
                    print(time.strftime('%F %T'), 'status:', event['text'], file=log)  # No request text is logged.
            try:
                self.window.evaluate_js('window.daevis && window.daevis.batch(%s)' % json.dumps(coalesce(batch), ensure_ascii=False))
            except Exception:
                pass

    def watch_show(self):
        """A second launch (double-click, Start menu) brings this window forward instead of opening another."""
        flag = Path.home() / '.daevis' / 'desktop' / 'show'
        while True:
            time.sleep(1)
            if flag.exists():
                flag.unlink(missing_ok=True)
                try:
                    self.window.restore()
                    self.window.show()
                except Exception:
                    pass

    def run(self, background):
        import webview
        config = relay.find_config()
        if config:
            self.start(config)
        self.window = webview.create_window('대비스', str(page()), js_api=self.api, width=1180, height=760,
                                            min_size=(940, 620), frameless=True, easy_drag=False,
                                            background_color='#04070c', minimized=background)
        self.api._window = self.window
        threading.Thread(target=self.pump, daemon=True).start()
        threading.Thread(target=self.watch_show, daemon=True).start()
        webview.start(private_mode=False, storage_path=str(Path.home() / '.daevis' / 'desktop' / 'webview'))


def command_line(argv):
    """`Daevis.exe ask ...` / `history`: print to the calling console, no window."""
    import ctypes
    from desktop import cli
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding='utf-8')  # Piped to another program or agent: UTF-8 JSON.
    elif ctypes.windll.kernel32.AttachConsole(-1):
        # Typed in a console: use its code page so Korean shows correctly there.
        page = ctypes.windll.kernel32.GetConsoleOutputCP()
        sys.stdout = open('CONOUT$', 'w', encoding=f'cp{page}', errors='replace')
    if sys.stderr is None:
        sys.stderr = sys.stdout
    config = relay.find_config()
    if not config:
        print('이 PC가 등록되지 않았어요. 앱을 한 번 열어 등록해 주세요.', file=sys.stderr)
        return 2
    client = relay.Relay(json.loads(config.read_text(encoding='utf-8-sig')))
    return cli.run(argv, client, sys.stdout)


def main():
    parser = argparse.ArgumentParser(description='Daevis desktop')
    parser.add_argument('--background', action='store_true', help='Start minimised')
    parser.add_argument('--agent', action='store_true', help='Headless voice and PC power agent (started at Windows login)')
    if len(sys.argv) > 1 and sys.argv[1] in ('ask', 'history'):
        raise SystemExit(command_line(sys.argv[1:]))
    if len(sys.argv) > 1 and sys.argv[1] == 'mcp':  # stdio MCP server for other agents; stdin/stdout are the client's pipes.
        from desktop import mcp_server
        mcp_server.main()
        return
    args = parser.parse_args()
    if args.agent:
        from desktop import agent
        raise SystemExit(agent.main())
    folder = Path.home() / '.daevis' / 'desktop'
    folder.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (folder / 'app.lock').open('a+b')
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        (folder / 'show').touch()  # Already running: ask that window to come forward.
        return
    Desktop().run(args.background)


if __name__ == '__main__':
    main()
