"""Paired A/B benchmark: real Hermes vs exact date/time replies, no Discord send.

Run on the main server with its owner-controlled environment loaded.
Uses temporary conversation history/outbox, not the owner's stored conversations.
"""
import json
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.gateway import Gateway
from agent.hermes_runner import HermesRunner
from agent.memory import Memory
from agent.artifacts import outbox_scope


def correct(case, reply):
    now = datetime.now(timezone(timedelta(hours=9)))
    if case == 'weekday':
        return ('월화수목금토일'[now.weekday()] + '요일') in reply
    if case == 'date':
        digits = re.findall(r'\d+', reply.split('\n\n')[0])
        return [str(now.year), str(now.month), str(now.day)] == [str(int(d)) for d in digits[:3]]
    # Permit both 12h and 24h rendering, with Korean or colon time separators.
    pattern = rf'(?<!\d)(?:{now.hour}|{now.hour % 12 or 12})\s*(?:시|:)\s*0?{now.minute}(?:분|\b)'
    return bool(re.search(pattern, reply.split('\n\n')[0]))


def main():
    root = Path(__file__).resolve().parents[1]
    samples = []
    with tempfile.TemporaryDirectory(prefix='daevis-simple-ab-') as folder:
        folder = Path(folder)
        for index, (case, query) in enumerate((('time', '지금 몇 시야?'), ('date', '오늘 날짜 알려줘'), ('weekday', '오늘 무슨 요일이야?'))):
            for mode in (('A', 'B') if index % 2 == 0 else ('B', 'A')):
                os.environ['DAEVIS_SIMPLE_REPLIES'] = 'off' if mode == 'A' else 'on'
                env = dict(os.environ)
                env['AGENT_OUTBOX'] = str(folder / f'out-{index}-{mode}')
                engine = HermesRunner(env['HERMES_PYTHON'], str(root / 'deploy/hermes-worker.py'), data=folder, env=env)
                memory = Memory(folder / f'{index}-{mode}.sqlite3')
                facade = SimpleNamespace(memory=memory, android=None, engine=engine)
                started = time.monotonic()
                try:
                    with outbox_scope(Path(env['AGENT_OUTBOX'])):
                        reply = Gateway.turn(facade, 'benchmark', query, lambda *a: False)
                    calls = re.search(r'모델 호출 (\d+)회', reply)
                    sample = {'mode': mode, 'case': case, 'ms': round((time.monotonic() - started) * 1000, 2),
                              'model_calls': int(calls[1]) if calls else None, 'correct': correct(case, reply)}
                except Exception as error:
                    sample = {'mode': mode, 'case': case, 'error': type(error).__name__}
                finally:
                    memory.db.close()
                samples.append(sample)
                print(json.dumps(sample), flush=True)
    for mode in ('A', 'B'):
        valid = [s for s in samples if s['mode'] == mode and 'ms' in s]
        print(json.dumps({'summary': mode, 'trials': len(valid), 'correct': sum(s['correct'] for s in valid),
                          'mean_ms': round(statistics.mean(s['ms'] for s in valid), 2) if valid else None,
                          'model_calls': sum(s['model_calls'] or 0 for s in valid)}), flush=True)


if __name__ == '__main__':
    main()
