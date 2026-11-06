#!/usr/bin/env python3
"""Run using Hermes's own Python environment, never the personal-agent venv."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time

protocol = sys.stdout
sys.argv[0] = str(Path(__file__).resolve())
def emit(value):
    print(json.dumps(value, ensure_ascii=False), file=protocol, flush=True)


def forwarded_env(env):
    return {key: env[key] for key in (
        'DAEVIS_MCP_PUBLIC', 'DAEVIS_MCP_OWNER_REQUEST', 'DAEVIS_MCP_REQUEST_ID',
        'DAEVIS_MCP_REQUEST_EXPIRES', 'DAEVIS_MCP_READ_ONLY', 'DAEVIS_MCP_ALLOW_TOOLS',
        'DAEVIS_MCP_APPROVAL', 'AGENT_OUTBOX') if key in env}


def self_send_receipt(name, value):
    if name == 'mcp__daevis__kakao_self_send' and value == 'KAKAO_SENT':
        return {'tool': name, 'confirmed': True, 'recipient_is_self': True}
    return None

def main():
    project = Path(__file__).resolve().parent.parent
    root = Path(os.environ.get("HERMES_REPO", Path.home()/".hermes/hermes-agent"))
    sys.path.insert(0,str(root))
    sys.path.insert(1,str(project/'deploy'))
    os.chdir(root)
    calls = [0]
    kakao = [False]
    receipts=[]
    from hermes_policy import TurnGuard, model_failure_reason
    guard=TurnGuard()
    active=[None]
    def interrupt(reason):
        if reason and active[0] is not None:
            active[0].interrupt(hard_cancel=True,tool_reason=reason)
    def step(*args):
        if active[0] is not None:
            interrupt(guard.before_step(getattr(active[0],'session_input_tokens',0)))
    def complete(call_id,name,args,result):
        calls[0] += 1
        # Do not export raw tool payloads, passwords or chat contents.
        rendered = json.dumps([args,result],ensure_ascii=False,default=str)
        kakao[0] = kakao[0] or 'kakao' in name.lower() or 'com.kakao.talk' in rendered or 'kakao' in rendered.lower()
        emit({'type':'tool','name':name,'kakao_used':kakao[0]})
        interrupt(guard.observe_tool(name,result))
        value=result
        for _ in range(5):
            if isinstance(value,str):
                try: value=json.loads(value)
                except ValueError: break
            elif isinstance(value,dict) and 'result' in value:
                value=value['result']
            else: break
        if name=='mcp__daevis__route_search' and isinstance(value,dict) and value.get('source')=='네이버 지도' and value.get('routes'):
            receipts.append({'tool':name,'confirmed':True,'origin':args.get('origin'),'destination':args.get('destination'),'depart_at':args.get('depart_at')})
        if name=='mcp__daevis__mail_send' and isinstance(value,str) and value.startswith('sent from '):
            own=os.environ.get('NAVER_ID','')
            own=own if '@' in own else own+'@naver.com'
            receipts.append({'tool':name,'confirmed':True,'recipient_is_self':not args.get('to') or args.get('to')==own})
        self_receipt = self_send_receipt(name, value)
        if self_receipt:
            receipts.append(self_receipt)
    with contextlib.redirect_stdout(sys.stderr):
        from run_agent import AIAgent
        from hermes_cli.auth_codex import resolve_codex_runtime_credentials
        from tools.mcp_tool_discovery import register_mcp_servers
        payload = json.load(sys.stdin)
        forwarded = forwarded_env(os.environ)
        discovered = register_mcp_servers({'daevis':{'command':'bash','args':[str(project/'deploy/run-mcp.sh')],
            'timeout':150,'connect_timeout':30,'supports_parallel_tool_calls':False,'env':forwarded}})
        if not discovered:
            raise RuntimeError('daevis MCP tools unavailable')
        emit({'type':'tools','tools':discovered,'public':os.environ.get('DAEVIS_MCP_PUBLIC')=='1'})
        creds = resolve_codex_runtime_credentials()
        agent = AIAgent(provider='openai-codex',api_key=creds['api_key'],base_url=creds['base_url'],
                        model=os.environ.get('AGENT_MODEL','gpt-5.6-luna'), enabled_toolsets=['mcp-daevis'],
                        max_iterations=12, max_tokens=1500, run_budget_seconds=120,
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        skip_background_review=True, load_soul_identity=False,
                        save_trajectories=False, tool_complete_callback=complete,step_callback=step)
        active[0]=agent
        response = agent.run_conversation(payload['text'],system_message=payload['instructions'],
                                          conversation_history=payload.get('history') or None)
    reason=guard.before_step(0) or model_failure_reason(response)
    emit({'type':'result','completed':bool(response.get('completed')) and reason is None,'stop_reason':reason,
          'text':response.get('final_response',''),'kakao_used':kakao[0],
          'input_tokens':None if reason in ('usage_limit_reached','model_auth_failed','model_rate_limited') else getattr(agent,'session_input_tokens',None),
          'output_tokens':None if reason in ('usage_limit_reached','model_auth_failed','model_rate_limited') else getattr(agent,'session_output_tokens',None),
          'model_calls':response.get('api_calls'),'tool_calls':calls[0],
          'model':str(getattr(agent,'model','미제공'))+' (설정)','receipts':receipts})

if __name__=='__main__':
    try: main()
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit({'type':'result','completed':False,'error':type(error).__name__})
        sys.exit(1)
