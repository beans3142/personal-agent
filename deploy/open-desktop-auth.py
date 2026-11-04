"""Open only the desktop app's official sign-in URL in its persistent browser."""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

url = sys.argv[1]
if urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in {'auth.openai.com', 'chatgpt.com'}:
    raise SystemExit('Unsupported sign-in destination')
root = Path(__file__).resolve().parent.parent
with sync_playwright() as p:
    binary = p.chromium.executable_path
# Match the existing Playwright browser runtime on this Ubuntu server.
os.execv(binary, [binary, '--no-sandbox', '--no-first-run', '--disable-gpu',
    '--user-data-dir=' + str(root / 'data/desktop-auth/browser'),
    '--remote-debugging-port=9351', '--remote-debugging-address=127.0.0.1',
    '--window-size=1200,850', url])
