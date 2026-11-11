"""run_code: one Docker container per run with limits, no secrets mounted."""
import json
import subprocess
from pathlib import Path

import pytest

from agent import sandbox


class FakeDocker:
    def __init__(self, output=b'hello\n', returncode=0, timeout=False, produce=None):
        self.output, self.returncode, self.timeout, self.produce = output, returncode, timeout, produce or {}
        self.calls, self.killed = [], []

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if 'kill' in argv:
            self.killed.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, b'')
        work = Path(next(a[:-len(':/work:rw')] for a in argv if a.endswith(':/work:rw')))
        assert (work / ('main.py' if 'python' in argv else 'main.sh')).exists()
        for name, data in self.produce.items():
            (work / 'out' / name).write_bytes(data)
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.returncode, self.output)


def run(tmp_path, docker, **args):
    box = sandbox.Sandbox(tmp_path / 'runs', docker=docker, docker_cmd=['docker'])
    outbox = tmp_path / 'outbox'
    return json.loads(box.run({'code': 'print(1)', **args}, outbox)), outbox


def test_container_has_limits_and_no_secrets(tmp_path):
    docker = FakeDocker()
    result, _ = run(tmp_path, docker)
    argv = docker.calls[0]
    joined = ' '.join(argv)
    assert result['exit_code'] == 0 and result['output'] == 'hello\n'
    for flag in ('--rm', '--read-only', '--cap-drop', 'ALL', 'no-new-privileges', '--memory', '512m', '--pids-limit', '--cpus'):
        assert flag in argv or flag in joined
    assert '--user' in argv and argv[argv.index('--user') + 1] != '0:0'
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a in ('-v', '--volume')]
    assert len(mounts) == 1 and mounts[0].endswith(':/work:rw')
    assert 'docker.sock' not in joined and '.env' not in joined and '/data' not in joined.replace(str(tmp_path), '')
    assert '--network' in argv and argv[argv.index('--network') + 1] == 'bridge'
    assert '--privileged' not in argv


def test_produced_files_go_to_the_owner(tmp_path):
    docker = FakeDocker(produce={'chart.png': b'PNG', 'data.csv': b'a,b'})
    result, outbox = run(tmp_path, docker)
    assert sorted(result['files']) == ['chart.png', 'data.csv']
    assert (outbox / 'files' / 'chart.png').read_bytes() == b'PNG'
    assert all(Path(p).exists() for p in result['paths'])


def test_input_files_are_copied_not_mounted(tmp_path):
    outbox = tmp_path / 'outbox'
    (outbox / 'files').mkdir(parents=True)
    (outbox / 'files' / 'sales.xlsx').write_bytes(b'XLSX')
    docker = FakeDocker()
    box = sandbox.Sandbox(tmp_path / 'runs', docker=docker, docker_cmd=['docker'])
    result = json.loads(box.run({'code': 'print(1)', 'inputs': ['sales.xlsx']}, outbox))
    work = Path(next(a[:-len(':/work:rw')] for a in docker.calls[0] if a.endswith(':/work:rw')))
    assert (work / 'in' / 'sales.xlsx').read_bytes() == b'XLSX' and result['exit_code'] == 0
    assert json.loads(box.run({'code': 'x', 'inputs': ['../../.env']}, outbox))['error']


def test_timeout_kills_the_container(tmp_path):
    docker = FakeDocker(timeout=True)
    result, _ = run(tmp_path, docker, timeout=5)
    assert result['timed_out'] is True and docker.killed and docker.killed[0].startswith('daevis-run-')


def test_output_is_truncated(tmp_path):
    result, _ = run(tmp_path, FakeDocker(output=b'x' * 50_000))
    assert len(result['output']) < 9000 and result['output'].endswith('[truncated]')


@pytest.mark.parametrize('args', [{'language': 'ruby'}, {'code': ''}, {'timeout': 9999}])
def test_bad_arguments(tmp_path, args):
    result, _ = run(tmp_path, FakeDocker(), **args)
    assert result.get('error')


def test_bash_runs_a_shell_script(tmp_path):
    docker = FakeDocker()
    run(tmp_path, docker, language='bash', code='echo hi')
    assert docker.calls[0][-2:] == ['bash', '/work/main.sh'] or 'bash' in docker.calls[0]


def test_old_runs_are_cleaned(tmp_path):
    import os, time
    old = tmp_path / 'runs' / 'old'
    old.mkdir(parents=True)
    os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    run(tmp_path, FakeDocker())
    assert not old.exists()


def test_tool_schema(tmp_path):
    [tool] = sandbox.build_sandbox_tools(lambda: tmp_path / 'outbox', {}, sandbox.Sandbox(tmp_path / 'runs', docker=FakeDocker(), docker_cmd=['docker']))
    assert tool.name == 'run_code' and tool.parameters['additionalProperties'] is False
    assert json.loads(tool.run({'code': 'print(1)'}))['exit_code'] == 0
