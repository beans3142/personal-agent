import os,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agent.hermes_runner import HermesRunner
from agent.gateway import instructions_for
from agent.memory import Memory
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='daevis-warm-smoke-') as folder:
    env={**os.environ,'DAEVIS_PREPARED_WORKER':'on','DAEVIS_PROMPT_CACHE':'off','AGENT_OUTBOX':folder+'/out'}
    runner=HermesRunner(env['HERMES_PYTHON'],str(root/'deploy/hermes-worker.py'),data=Path(folder),env=env)
    memory=Memory(Path(folder)/'memory.sqlite3')
    try:
        ready=runner.execute.wait_ready(30)
        outcome=runner.run('준비 완료라고만 답해줘.',instructions_for(memory),[],chat_scope='benchmark:warm-smoke')
        print(json.dumps({'prepared_ready':ready,'warm_hits':runner.execute.hits,'correct':'준비 완료' in outcome.text,'model_calls':outcome.model_calls,'tool_calls':outcome.tool_calls}))
    finally:
        runner.execute.close();memory.db.close()
