"""Shows the server's Android screen in a private web page so the owner can sign in to apps.

Typed text goes straight to Android. This code does not log or store it.
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .qr_login import discord_sender
from .android_control import APPS, UiaDevice

WEB_PORT = 6090
OPEN_SECONDS = 1800
KEYS = {"BACK": "KEYCODE_BACK", "HOME": "KEYCODE_HOME", "ENTER": "KEYCODE_ENTER", "DEL": "KEYCODE_DEL", "TAB": "KEYCODE_TAB"}
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Android</title><style>body{margin:0;background:#111;color:#eee;font:16px sans-serif}img{width:100%;display:block}
.bar{display:flex;gap:6px;padding:8px;flex-wrap:wrap}input{flex:1;min-width:140px;font-size:16px;padding:8px}button{font-size:16px;padding:8px 12px}</style>
<div class=bar><input id=t type=password placeholder="입력할 글자" autocomplete=off><button onclick=toggle()>보기</button><button onclick=send()>입력</button>
<button onclick="key('ENTER')">Enter</button><button onclick="key('DEL')">지우기</button><button onclick="key('TAB')">Tab</button><button onclick="key('BACK')">뒤로</button><button onclick="key('HOME')">홈</button></div>
<div class=bar><button onclick="app('kakao')">카카오톡</button><button onclick="app('baemin')">배민</button><button onclick="app('play_store')">Play 스토어</button>
<button onclick="app('kakao',true)">카톡 설치</button><button onclick="app('baemin',true)">배민 설치</button></div>
<img id=s src=screen.png><script>
const s=document.getElementById('s'),t=document.getElementById('t');
let busy=false;
function refresh(){if(busy)return;busy=true;const i=new Image();i.onload=()=>{s.src=i.src;busy=false};i.onerror=()=>{busy=false};i.src='screen.png?'+Date.now()}
setInterval(refresh,1500);
s.onclick=e=>{const r=s.getBoundingClientRect(),x=Math.round((e.clientX-r.left)*s.naturalWidth/r.width),y=Math.round((e.clientY-r.top)*s.naturalHeight/r.height);
s.style.opacity=.6;fetch(`tap?x=${x}&y=${y}`,{method:'POST'}).then(()=>{s.style.opacity=1;refresh()})};
function send(){fetch('text',{method:'POST',body:t.value}).then(()=>{t.value='';refresh()})}
function key(n){fetch('key?name='+n,{method:'POST'}).then(refresh)}
function toggle(){t.type=t.type==='password'?'text':'password'}
function app(name,store=false){fetch('app?name='+name+'&store='+(store?'1':'0'),{method:'POST'}).then(refresh)}
</script>"""


def input_text_arg(value: str) -> str:
    """Encodes spaces for Android's `input text` and quotes for the adb shell."""
    return shlex.quote(value.replace(" ", "%s"))


class Android:
    """Talks to Android over one adb connection, which is much lighter than `waydroid shell`."""

    def __init__(self, serial: str):
        self.serial = serial

    def _adb(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["adb", "-s", self.serial, *args], capture_output=True, timeout=30)

    def screen(self) -> bytes:
        return self._adb("exec-out", "screencap", "-p").stdout

    def tap(self, x: int, y: int) -> None:
        self._adb("shell", "input", "tap", str(x), str(y))

    def text(self, value: str) -> None:
        self._adb("shell", "input", "text", input_text_arg(value))

    def key(self, name: str) -> None:
        self._adb("shell", "input", "keyevent", KEYS[name])

    def open_app(self, name: str, store: bool = False) -> None:
        UiaDevice(self.serial).open(name, store)


def adb_serial() -> str:
    status = subprocess.run(["waydroid", "status"], capture_output=True, text=True, timeout=30).stdout
    address = re.search(r"IP address:\s*([0-9.]+)", status)
    if not address:
        raise RuntimeError("Android IP address not found")
    serial = f"{address.group(1)}:5555"
    subprocess.run(["adb", "connect", serial], capture_output=True, timeout=30)
    return serial


class PortalServer(ThreadingHTTPServer):
    def __init__(self, address, android, token: str):
        self.android = android
        self.token = token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def _route(self) -> tuple[str, dict] | None:
        parsed = urlparse(self.path)
        prefix = f"/{self.server.token}/"
        if not parsed.path.startswith(prefix):
            self._reply(404, b"not found")
            return None
        return parsed.path[len(prefix):], parse_qs(parsed.query)

    def _reply(self, status: int, body: bytes = b"", kind: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self._route()
        if route is None:
            return
        if route[0] == "":
            self._reply(200, PAGE.encode(), "text/html; charset=utf-8")
        elif route[0] == "screen.png":
            self._reply(200, self.server.android.screen(), "image/png")
        else:
            self._reply(404)

    def do_POST(self):
        route = self._route()
        if route is None:
            return
        path, query = route
        android = self.server.android
        if path == "tap":
            try:
                x, y = int(query["x"][0]), int(query["y"][0])
            except (KeyError, ValueError):
                return self._reply(400)
            if x < 0 or y < 0:
                return self._reply(400)
            android.tap(x, y)
        elif path == "text":
            android.text(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace"))
        elif path == "key":
            if query.get("name", [""])[0] not in KEYS:
                return self._reply(400)
            android.key(query["name"][0])
        elif path == "app":
            name = query.get("name", [""])[0]
            if name not in APPS:
                return self._reply(400)
            android.open_app(name, query.get("store", ["0"])[0] == "1")
        else:
            return self._reply(404)
        self._reply(204)

    def log_message(self, *args):
        pass


def main() -> None:
    token = secrets.token_urlsafe(24)
    server = PortalServer(("127.0.0.1", WEB_PORT), Android(adb_serial()), token)
    log = Path("/tmp/android-tunnel.log")
    tunnel = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{WEB_PORT}"],
                              stdout=log.open("w"), stderr=subprocess.STDOUT)
    url = None
    for _ in range(60):
        found = TUNNEL_PATTERN.search(log.read_text(errors="ignore"))
        if found:
            url = found.group(0)
            break
        time.sleep(1)
    if url is None:
        tunnel.terminate()
        raise RuntimeError("HTTPS tunnel did not start")
    send = discord_sender(os.environ["DISCORD_BOT_TOKEN"], os.environ["OWNER_ID"])
    send(f"서버 안드로이드 화면이에요. 30분 뒤 닫혀요. 링크를 다른 사람에게 보내지 마세요.\n{url}/{token}/")
    import threading

    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"{url}/{token}/", flush=True)
    time.sleep(OPEN_SECONDS)
    server.shutdown()
    tunnel.terminate()
    send("서버 안드로이드 화면을 닫았어요.")


if __name__ == "__main__":
    main()
