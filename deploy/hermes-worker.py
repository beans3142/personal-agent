#!/usr/bin/env python3
"""Run using Hermes's own Python environment, never the personal-agent venv."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time

protocol = sys.stdout
prepared = '--prepared' in sys.argv
startup_probe = '--startup-probe' in sys.argv
if prepared:
    from setproctitle import setproctitle
    setproctitle('daevis-prepared (idle)')
sys.argv[0] = str(Path(__file__).resolve())
def emit(value):
    print(json.dumps(value, ensure_ascii=False), file=protocol, flush=True)


def forwarded_env(env):
    return {key: env[key] for key in (
        'DAEVIS_MCP_PUBLIC', 'DAEVIS_MCP_OWNER_REQUEST', 'DAEVIS_MCP_REQUEST_ID',
        'DAEVIS_MCP_REQUEST_EXPIRES', 'DAEVIS_MCP_READ_ONLY', 'DAEVIS_MCP_ALLOW_TOOLS','DAEVIS_MCP_CURRENT_TEXT','DAEVIS_MCP_CHAT_SCOPE',
        'DAEVIS_MCP_APPROVAL', 'AGENT_OUTBOX') if key in env}


def provider_settings(env, codex):
    # The runner sets these only for a Claude retry after a ChatGPT usage limit.
    if env.get('DAEVIS_HERMES_PROVIDER')=='anthropic' and env.get('DAEVIS_CLAUDE_TOKEN'):
        return {'provider':'anthropic','api_key':env['DAEVIS_CLAUDE_TOKEN'],'base_url':'https://api.anthropic.com',
                'model':env.get('DAEVIS_CLAUDE_MODEL') or 'claude-sonnet-5-5'}
    creds=codex()
    return {'provider':'openai-codex','api_key':creds['api_key'],'base_url':creds['base_url'],
            'model':env.get('AGENT_MODEL','gpt-5.6-luna')}


def self_send_receipt(name, value):
    if name == 'mcp__daevis__kakao_self_send' and value == 'KAKAO_SENT':
        return {'tool': name, 'confirmed': True, 'recipient_is_self': True}
    return None


def owner_control_receipt(name,args,value):
    if name!='mcp__daevis__owner_control' or not isinstance(value,dict):return None
    action=args.get('action')
    if action=='preferences' and isinstance(value.get('preferences'),dict):
        values=value['preferences']
        if values and all(k in ('trend_daily','trend_weekly','tech','jobs','events') and type(v) is bool for k,v in values.items()):
            return {'tool':name,'action':action,'preferences':values,'changed':list((args.get('changes') or {}).keys())}
    if action not in ('pc_shutdown','pc_cancel','pc_wake','server_stop'):return None
    if value.get('status') not in ('queued','dispatched','succeeded','completed','scheduled') and value.get('packet_sent') is not True:return None
    receipt={'tool':name,'action':action,'accepted':True,'execution_confirmed':False}
    if action=='pc_shutdown' and value.get('tracking') is True and str(value.get('status_message_id','')).isdigit():
        receipt.update(tracking=True,status_message_id=str(value['status_message_id']))
    return receipt


def kakao_activity(name,args,result):
    # Privacy mode hides the answer, so trigger it only for Kakao tools or
    # Kakao app screens; shop pages that merely mention Kakao Pay don't count.
    rendered=json.dumps([args,result],ensure_ascii=False,default=str)
    return 'kakao' in name.lower() or (isinstance(args,dict) and args.get('app')=='kakao') or 'com.kakao.talk' in rendered


def unwrap_tool_value(value):
    for _ in range(8):
        if isinstance(value,str):
            try:value=json.loads(value)
            except ValueError:break
        elif isinstance(value,dict) and value.get('isError'):return None
        elif isinstance(value,dict) and 'result' in value:value=value['result']
        elif isinstance(value,dict) and isinstance(value.get('content'),list) and len(value['content'])==1:
            block=value['content'][0]
            if not isinstance(block,dict) or block.get('type')!='text':break
            value=block.get('text','')
        else:break
    return value

def main():
    project = Path(__file__).resolve().parent.parent
    root = Path(os.environ.get("HERMES_REPO", Path.home()/".hermes/hermes-agent"))
    sys.path.insert(0,str(root))
    sys.path.insert(1,str(project/'deploy'))
    os.chdir(root)
    calls = [0]
    kakao = [False]
    receipts=[]
    control_complete=[False]
    from hermes_policy import MAX_ITERATIONS, TurnGuard, limit_reset_at, model_failure_reason
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
        kakao[0] = kakao[0] or kakao_activity(name,args,result)
        emit({'type':'tool','name':name,'kakao_used':kakao[0]})
        interrupt(guard.observe_tool(name,result))
        value=unwrap_tool_value(result)
        if name=='mcp__daevis__route_search' and isinstance(value,dict) and value.get('source')=='네이버 지도' and value.get('routes'):
            receipts.append({'tool':name,'confirmed':True,'origin':args.get('origin'),'destination':args.get('destination'),'depart_at':args.get('depart_at')})
        if name=='mcp__daevis__mail_send' and isinstance(value,str) and value.startswith('sent from '):
            own=os.environ.get('NAVER_ID','')
            own=own if '@' in own else own+'@naver.com'
            receipts.append({'tool':name,'confirmed':True,'recipient_is_self':not args.get('to') or args.get('to')==own})
        self_receipt = self_send_receipt(name, value)
        if self_receipt:
            receipts.append(self_receipt)
        control_receipt=owner_control_receipt(name,args,value)
        if control_receipt:
            receipts.append(control_receipt)
            if control_receipt.get('action')=='server_stop':
                control_complete[0]=True;interrupt('owner_control_complete')
    with contextlib.redirect_stdout(sys.stderr):
        # Hermes may re-exec this script while importing, so read the provider
        # settings only afterwards, then drop the Claude token before MCP starts.
        from run_agent import AIAgent
        from hermes_cli.auth_codex import resolve_codex_runtime_credentials
        from tools.mcp_tool_discovery import register_mcp_servers
        if prepared:
            emit({'type':'prepared'})
        payload = json.load(sys.stdin)
        if prepared:
            request_env = payload.pop('_prepared_env')
            if not isinstance(request_env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in request_env.items()):
                raise ValueError('invalid prepared request environment')
            os.environ.clear();os.environ.update(request_env)
            setproctitle('hermes-worker.py (prepared active)')
        if startup_probe:
            emit({'type':'probe','request_id':os.environ.get('DAEVIS_MCP_REQUEST_ID','')})
            return
        provider_env={k:os.environ.pop(k) for k in ('DAEVIS_HERMES_PROVIDER','DAEVIS_CLAUDE_TOKEN','DAEVIS_CLAUDE_MODEL') if k in os.environ}
        forwarded = forwarded_env(os.environ)
        discovered = register_mcp_servers({'daevis':{'command':'bash','args':[str(project/'deploy/run-mcp.sh')],
            'timeout':260,'connect_timeout':30,'supports_parallel_tool_calls':False,'env':forwarded}})
        if not discovered:
            raise RuntimeError('daevis MCP tools unavailable')
        emit({'type':'tools','tools':discovered,'public':os.environ.get('DAEVIS_MCP_PUBLIC')=='1'})
        settings = provider_settings({**provider_env,'AGENT_MODEL':os.environ.get('AGENT_MODEL','gpt-5.6-luna')},resolve_codex_runtime_credentials)
        agent = AIAgent(**settings, enabled_toolsets=['mcp-daevis'],
                        max_iterations=MAX_ITERATIONS, max_tokens=1500, run_budget_seconds=270,
                        quiet_mode=True, skip_context_files=True, skip_memory=True,
                        gateway_session_key=payload.get('cache_scope'),
                        skip_background_review=True, load_soul_identity=False,
                        save_trajectories=False, tool_complete_callback=complete,step_callback=step)
        active[0]=agent
        response = agent.run_conversation(payload['text'],system_message=payload['instructions'],
                                          conversation_history=payload.get('history') or None)
    reason=None if control_complete[0] else guard.before_step(0) or model_failure_reason(response, MAX_ITERATIONS)
    emit({'type':'result','completed':(control_complete[0] or bool(response.get('completed'))) and reason is None,'stop_reason':reason,
          'text':response.get('final_response',''),'kakao_used':kakao[0],
          'input_tokens':None if reason in ('usage_limit_reached','model_auth_failed','model_rate_limited') else getattr(agent,'session_input_tokens',None),
          'output_tokens':None if reason in ('usage_limit_reached','model_auth_failed','model_rate_limited') else getattr(agent,'session_output_tokens',None),
          'cache_read_tokens':getattr(agent,'session_cache_read_tokens',None),
          'cache_write_tokens':getattr(agent,'session_cache_write_tokens',None),
          'model_calls':response.get('api_calls'),'tool_calls':calls[0],
          'model':str(getattr(agent,'model','미제공'))+' (설정)','receipts':receipts,
          'provider':settings['provider'],'resets_at':limit_reset_at(response) if reason=='usage_limit_reached' else None})

if __name__=='__main__':
    try: main()
    except Exception as error:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit({'type':'result','completed':False,'error':type(error).__name__})
        sys.exit(1)
