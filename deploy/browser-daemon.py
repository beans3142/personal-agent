#!/usr/bin/env python3
"""Public-map browser, loopback CDP only; not the owner's login profile."""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(os.environ.get('AGENT_DATA',Path(__file__).resolve().parent.parent/'data'))
profile=root/'browser-service-profile'
profile.mkdir(parents=True,exist_ok=True,mode=0o700)
with sync_playwright() as p:
    executable=p.chromium.executable_path
os.execv(executable,[executable,'--headless=new','--no-sandbox','--disable-dev-shm-usage',
    '--remote-debugging-address=127.0.0.1','--remote-debugging-port=9222',
    '--user-data-dir='+str(profile),'--no-first-run','--disable-background-networking','about:blank'])
