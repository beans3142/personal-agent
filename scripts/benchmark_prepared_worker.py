"""Three paired startup-only A/B trials; no provider request, no MCP action."""
import json, os, statistics, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.hermes_runner import run_process
from agent.warm_worker import PreparedExecutor

root=Path(__file__).resolve().parents[1]
argv=[os.environ['HERMES_PYTHON'],str(root/'deploy/hermes-worker.py'),'--startup-probe']
samples=[]
for i in range(3):
    env={**os.environ,'DAEVIS_MCP_REQUEST_ID':f'benchmark-{i}'}
    t=time.monotonic()
    cold=run_process(argv,input='{}',text=True,encoding='utf-8',env=env,timeout=30,start_new_session=True)
    a=(time.monotonic()-t)*1000
    t=time.monotonic();pool=PreparedExecutor(argv,env=env)
    try:
        assert pool.wait_ready(30),'standby not ready'
        prepared_ms=(time.monotonic()-t)*1000
        pid=pool.slot['process'].pid
        status=Path(f'/proc/{pid}/status').read_text()
        rss=next(int(line.split()[1]) for line in status.splitlines() if line.startswith('VmRSS:'))
        t=time.monotonic()
        warm=pool(argv,input='{}',text=True,encoding='utf-8',env=env,timeout=30,start_new_session=True)
        b=(time.monotonic()-t)*1000
        def valid(result):
            return result.returncode==0 and any(json.loads(s).get('request_id')==f'benchmark-{i}' for s in result.stdout.splitlines() if s.startswith('{'))
        row={'trial':i+1,'A_ms':round(a,2),'B_ms':round(b,2),'preparation_ms':round(prepared_ms,2),'standby_rss_mb':round(rss/1024,1),'correct':valid(cold) and valid(warm)}
        samples.append(row);print(json.dumps(row),flush=True)
    finally:pool.close()
print(json.dumps({'mean_A_ms':round(statistics.mean(r['A_ms'] for r in samples),2),'mean_B_ms':round(statistics.mean(r['B_ms'] for r in samples),2)}))
