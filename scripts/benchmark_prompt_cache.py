"""Alternating cache-scope A/B calls; identical prompt, no stored conversation output."""
import json, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.gateway import instructions_for
from agent.memory import Memory
from agent.hermes_runner import HermesRunner
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='daevis-cache-ab-') as folder:
    memory=Memory(Path(folder)/'memory.sqlite3')
    instructions=instructions_for(memory)
    try:
        for i,mode in enumerate(('A','B','A','B')):
            env={**os.environ,'DAEVIS_PROMPT_CACHE':'on' if mode=='B' else 'off','DAEVIS_PREPARED_WORKER':'off',
                 'AGENT_OUTBOX':str(Path(folder)/f'out-{i}')}
            runner=HermesRunner(env['HERMES_PYTHON'],str(root/'deploy/hermes-worker.py'),data=Path(folder),env=env)
            t=time.monotonic()
            try:
                r=runner.run('준비 완료라고만 답해줘.',instructions,[],chat_scope='benchmark:cache')
                print(json.dumps({'mode':mode,'trial':i+1,'ms':round((time.monotonic()-t)*1000,2),
                    'input_tokens':r.input_tokens,'output_tokens':r.output_tokens,'cache_read_tokens':r.cache_read_tokens,
                    'cache_write_tokens':r.cache_write_tokens,'correct':'준비 완료' in r.text}),flush=True)
            except Exception as error:
                print(json.dumps({'mode':mode,'trial':i+1,'error':type(error).__name__}),flush=True)
    finally:memory.db.close()
