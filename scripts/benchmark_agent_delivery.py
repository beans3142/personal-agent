"""Controlled local benchmark of the main-agent delivery gap; never runs a model/tool.

python scripts/benchmark_agent_delivery.py --trials 8
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.pc_control import PCRegistry


def measure(registry, token, mode, offset, key):
    ready = threading.Event()
    received = []
    errors = []

    def receiver():
        try:
            if mode == 'poll_2s':
                job = registry.poll('agent-main', token)
                assert job is None
                ready.set()
                time.sleep(2)
                job = registry.poll('agent-main', token)
            else:
                ready.set()
                job = registry.agent_poll('agent-main', token, wait=5)
            received.append((job, time.monotonic()))
        except Exception as error:
            errors.append(type(error).__name__)
            ready.set()

    thread = threading.Thread(target=receiver, daemon=True)
    thread.start()
    assert ready.wait(5)
    time.sleep(offset)
    enqueued = time.monotonic()
    sent = registry.forward_to_agent('benchmark only', key, 'typed')
    thread.join(7)
    if thread.is_alive() or errors or not received or not received[0][0]:
        raise RuntimeError(f'benchmark receiver failed: {errors}')
    job, at = received[0]
    assert job['id'] == sent['id']
    registry.ack('agent-main', token, job['id'], 'succeeded', {'benchmark': True})
    return round((at - enqueued) * 1000, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trials', type=int, default=8)
    args = parser.parse_args()
    if not 2 <= args.trials <= 30:
        parser.error('trials must be 2..30')
    result = {}
    with tempfile.TemporaryDirectory(prefix='daevis-delivery-') as folder:
        registry = PCRegistry(Path(folder) / 'pc.sqlite3')
        registry.ensure_voice()
        token = registry.enroll_agent()
        for mode in ('poll_2s', 'long_poll_150ms'):
            samples = [measure(registry, token, mode, .1 + 1.75 * i / (args.trials - 1), f'{mode}-{i}')
                       for i in range(args.trials)]
            result[mode] = {'trials': len(samples), 'mean_ms': round(statistics.mean(samples), 2),
                            'max_ms': max(samples), 'samples_ms': samples}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
