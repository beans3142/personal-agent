"""Signs in by QR code: the owner scans it with their phone. No password is entered."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from .authgate import SITES, PlaywrightBrowser, Site

API = "https://discord.com/api/v10"
POLL_SECONDS = 5
QR_LIFETIME_POLLS = 30
TOTAL_POLLS = 120
QR_SITES = {
    "naver": "https://nid.naver.com/nidlogin.login?mode=qrcode",
    "discord": "https://discord.com/login",
}


def qr_login(site: Site, qr_url: str, browser, send_image, shot: Path, wait=lambda: time.sleep(POLL_SECONDS),
             attempts: int = TOTAL_POLLS, refresh_every: int = QR_LIFETIME_POLLS) -> str:
    page = browser.open(site)
    try:
        for attempt in range(attempts):
            if attempt % refresh_every == 0:
                page.goto(qr_url)
                page.poll()
                page.screenshot(shot)
                send_image(f"{site.name} 로그인 QR이에요. 휴대폰 앱으로 찍어 주세요.", shot)
            if page.has(site.logged_in_selector):
                browser.save(site)
                return "ok"
            wait()
            page.poll()
        return "timeout"
    finally:
        browser.close()


def discord_sender(token: str, owner_id: str):
    headers = {"Authorization": f"Bot {token}", "User-Agent": "personal-agent/1.0"}

    def send(text: str, path: Path | None = None) -> None:
        channel = httpx.post(f"{API}/users/@me/channels", headers=headers, json={"recipient_id": owner_id}, timeout=20)
        channel.raise_for_status()
        files = {"files[0]": (path.name, path.read_bytes(), "image/png")} if path else None
        response = httpx.post(
            f"{API}/channels/{channel.json()['id']}/messages",
            headers=headers,
            data={"payload_json": json.dumps({"content": text})},
            files=files,
            timeout=30,
        )
        response.raise_for_status()

    return send


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", choices=list(QR_SITES))
    args = parser.parse_args()
    data = Path(os.environ.get("AGENT_DATA", "data"))
    send = discord_sender(os.environ["DISCORD_BOT_TOKEN"], os.environ["OWNER_ID"])
    site = SITES[args.site]
    result = qr_login(site, QR_SITES[args.site], PlaywrightBrowser(data / "sessions"), send, Path(f"/tmp/qr-{args.site}.png"))
    send(f"{args.site} QR 로그인 결과: {'완료' if result == 'ok' else '시간 초과'}")
    print(result)
    sys.exit(0 if result == "ok" else 1)


if __name__ == "__main__":
    main()
