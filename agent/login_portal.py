"""Lets the owner sign in once by hand in a browser on this server, then saves the session.

The owner opens a private HTTPS link and types directly into the server's browser.
This code never reads or stores what is typed; it saves only the resulting session.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .authgate import SITES
from .qr_login import discord_sender

DISPLAY = ":2"
CDP_PORT = 9333
VNC_PORT = 5902
WEB_PORT = 6082
TIMEOUT_SECONDS = 1200
POLL_SECONDS = 5
SESSION_MARKERS = {
    "naver": "NID_AUT",
    "kakao": "_kawlt",
    "google": "SAPISID",
    "discord": "__sdcfduid",
}
TUNNEL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def is_logged_in(context, site: str) -> bool:
    return any(cookie["name"] == SESSION_MARKERS[site] for cookie in context.cookies())


def tunnel_url_from(line: str) -> str | None:
    match = TUNNEL_PATTERN.search(line)
    return match.group(0) if match else None


def start(command: list[str], log: Path | None = None) -> subprocess.Popen:
    output = log.open("w") if log else subprocess.DEVNULL
    return subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)


def wait_for_tunnel(log: Path, timeout: int = 60) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = tunnel_url_from(log.read_text(errors="ignore")) if log.exists() else None
        if found:
            return found
        time.sleep(1)
    raise RuntimeError("HTTPS tunnel did not start")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", choices=list(SESSION_MARKERS))
    parser.add_argument("--url", help="page to open instead of the login page")
    parser.add_argument("--stay", action="store_true", help="stay open until the timeout, saving the session as it goes")
    args = parser.parse_args()
    site = SITES[args.site]
    data = Path(os.environ.get("AGENT_DATA", "data")).resolve()
    state = data / "sessions" / f"{site.name}.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    send = discord_sender(os.environ["DISCORD_BOT_TOKEN"], os.environ["OWNER_ID"])
    screen_password = secrets.token_urlsafe(6)[:8]
    tunnel_log = Path(f"/tmp/portal-tunnel-{site.name}.log")
    env = {**os.environ, "DISPLAY": DISPLAY}
    processes = [start(["Xvfb", DISPLAY, "-screen", "0", "1100x900x24"])]
    time.sleep(2)
    try:
        with sync_playwright() as playwright:
            chromium = playwright.chromium.executable_path
            processes.append(subprocess.Popen(
                [chromium, f"--user-data-dir={data / 'portal-profile' / site.name}", f"--remote-debugging-port={CDP_PORT}",
                 "--remote-debugging-address=127.0.0.1", "--no-first-run", "--window-size=1100,900", "--window-position=0,0",
                 "--lang=ko-KR", args.url or site.login_url],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ))
            time.sleep(5)
            processes.append(start(["x11vnc", "-display", DISPLAY, "-localhost", "-rfbport", str(VNC_PORT), "-passwd", screen_password, "-forever", "-shared", "-quiet"]))
            processes.append(start(["websockify", "--web=/usr/share/novnc", f"127.0.0.1:{WEB_PORT}", f"127.0.0.1:{VNC_PORT}"]))
            processes.append(start(["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{WEB_PORT}"], tunnel_log))
            url = wait_for_tunnel(tunnel_log)
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            context = browser.contexts[0]
            send(f"{site.name} 로그인 화면이에요. 한 번만 직접 로그인해 주세요. 20분 뒤 닫혀요.\n{url}/vnc.html?autoconnect=1&resize=scale\n화면 암호: {screen_password}")
            deadline = time.time() + TIMEOUT_SECONDS
            while time.time() < deadline:
                if args.stay:
                    if is_logged_in(context, site.name):
                        context.storage_state(path=str(state))
                        state.chmod(0o600)
                    time.sleep(POLL_SECONDS)
                    continue
                if is_logged_in(context, site.name):
                    time.sleep(3)
                    context.storage_state(path=str(state))
                    state.chmod(0o600)
                    send(f"{site.name} 로그인을 저장했어요. 이제 로그인 화면은 닫아요.")
                    print("saved")
                    return
                time.sleep(POLL_SECONDS)
            send(f"{site.name} 화면이 20분이 지나 닫혔어요.")
            print("timeout")
    finally:
        for process in reversed(processes):
            process.terminate()


if __name__ == "__main__":
    main()
