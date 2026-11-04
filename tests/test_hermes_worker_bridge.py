import importlib.util
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def worker_module():
    path = Path(__file__).parents[1] / 'deploy/hermes-worker.py'
    spec = importlib.util.spec_from_file_location('test_worker_bridge', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_forwards_request_id_but_not_unrelated_credentials():
    worker = worker_module()
    assert worker.forwarded_env({'DAEVIS_MCP_REQUEST_ID': 'trusted', 'PASSWORD': 'secret'}) == {
        'DAEVIS_MCP_REQUEST_ID': 'trusted'}


def test_self_receipt_requires_exact_native_confirmation_and_hides_body():
    worker = worker_module()
    assert worker.self_send_receipt('mcp__daevis__kakao_self_send', 'KAKAO_SENT') == {
        'tool': 'mcp__daevis__kakao_self_send', 'confirmed': True, 'recipient_is_self': True}
    assert worker.self_send_receipt('mcp__daevis__kakao_self_send', 'KAKAO_SENT\nprivate body') is None
    assert worker.self_send_receipt('mcp__daevis__kakao_self_send', 'error: uncertain') is None
    assert worker.self_send_receipt('mcp__daevis__android_input', 'KAKAO_SENT') is None


def test_mcp_launcher_preserves_trusted_request_id_after_loading_env(tmp_path):
    git_bash = Path('C:/Program Files/Git/bin/bash.exe')
    bash = str(git_bash) if os.name == 'nt' and git_bash.exists() else shutil.which('bash')
    if not bash:
        pytest.skip('bash is unavailable')
    deployment = tmp_path / 'deploy'
    deployment.mkdir()
    source = Path(__file__).parents[1] / 'deploy/run-mcp.sh'
    launcher = deployment / 'run-mcp.sh'
    launcher.write_bytes(source.read_bytes().replace(b'\r\n', b'\n'))
    (tmp_path / '.env').write_text('DAEVIS_MCP_REQUEST_ID=stale-config-id\n')
    interpreter = tmp_path / '.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('#!/usr/bin/env bash\nprintf "%s" "$DAEVIS_MCP_REQUEST_ID"\n')
    interpreter.chmod(0o700)
    completed = subprocess.run([bash, str(launcher)], env={**os.environ, 'DAEVIS_MCP_REQUEST_ID': 'trusted-caller-id'},
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == 'trusted-caller-id'
