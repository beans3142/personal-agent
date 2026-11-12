import json, os, subprocess, sys, time
from pathlib import Path
import pytest
from agent.warm_worker import PreparedExecutor


def worker(tmp_path):
    p = tmp_path / 'worker.py'
    p.write_text('''import os,sys,json,time
if '--prepared' in sys.argv:
 assert os.environ.get('DAEVIS_MCP_REQUEST_ID') is None
 print(json.dumps({'type':'prepared'}),flush=True)
payload=json.load(sys.stdin)
if '--prepared' in sys.argv:
 os.environ.clear();os.environ.update(payload.pop('_prepared_env'))
print(json.dumps({'lease':os.environ.get('DAEVIS_MCP_REQUEST_ID'), 'text':payload['text']}),flush=True)
''')
    return p


def test_prepared_worker_receives_fresh_scope_once_and_is_replaced(tmp_path):
    argv=[sys.executable,str(worker(tmp_path))]
    pool=PreparedExecutor(argv,env={**os.environ,'DAEVIS_MCP_REQUEST_ID':'stale'})
    try:
        for n in range(2):
            assert pool.wait_ready(5)
            reply=pool(argv,input=json.dumps({'text':str(n)}),text=True,encoding='utf-8',
                       env={**os.environ,'DAEVIS_MCP_REQUEST_ID':str(n)},timeout=5,start_new_session=True)
            assert json.loads(reply.stdout)=={'lease':str(n),'text':str(n)}
        assert pool.hits==2
    finally:pool.close()


def test_failed_standby_falls_back_before_handing_over_request(tmp_path):
    p=tmp_path/'worker.py'
    p.write_text("import sys,json\nif '--prepared' in sys.argv:sys.exit(1)\nx=json.load(sys.stdin);print(json.dumps(x))\n")
    argv=[sys.executable,str(p)]
    pool=PreparedExecutor(argv,env=dict(os.environ))
    try:
        assert not pool.wait_ready(5)
        value=pool(argv,input='{"text":"safe"}',text=True,encoding='utf-8',env=dict(os.environ),timeout=5,start_new_session=True)
        assert json.loads(value.stdout)=={'text':'safe'} and pool.hits==0
    finally:pool.close()


def test_prepared_worker_never_retries_after_request_execution(tmp_path):
    p=tmp_path/'worker.py'
    p.write_text("import sys,json,time\nprint(json.dumps({'type':'prepared'}),flush=True)\njson.load(sys.stdin)\ntime.sleep(20)\n")
    calls=[]
    def cold(*a,**kw):calls.append(1);pytest.fail('must not replay')
    argv=[sys.executable,str(p)]
    pool=PreparedExecutor(argv,env=dict(os.environ),cold=cold)
    try:
        assert pool.wait_ready(5)
        with pytest.raises(subprocess.TimeoutExpired):
            pool(argv,input='{}',text=True,encoding='utf-8',env=dict(os.environ),timeout=.2,start_new_session=True)
        assert calls==[]
    finally:pool.close()
