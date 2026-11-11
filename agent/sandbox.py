"""run_code: Python or bash in a throwaway Docker container on the main server.

Each run gets its own folder mounted at /work (code, `in/` copies of this request's
files, `out/` for results) and nothing else: no .env, no data/, no Docker socket.
The container runs as an unprivileged user with a read-only root filesystem, all
capabilities dropped, and memory/CPU/process/time limits sized to leave room for
ReDroid and n8n on the 4 GB host. Internet access is allowed (pip install works,
into /work). Files written to /work/out are sent to the owner.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from .loop import Tool

try:
    import fcntl
except ImportError:  # Windows test runs; the server is Linux.
    fcntl = None

RUNS = '/tmp/daevis-runs'
IMAGE = 'daevis-sandbox:1'
MEMORY, SWAP, CPUS, PIDS = '512m', '768m', '1.0', '256'
OUTPUT_LIMIT = 8000
MAX_FILES, FILE_LIMIT = 10, 25 * 1024 * 1024
KEEP_SECONDS = 86400


def _docker(argv, timeout):
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)


class Sandbox:
    def __init__(self, runs=RUNS, docker=_docker, docker_cmd=None, image=IMAGE, user=None):
        self.runs, self.docker, self.image = Path(runs), docker, image
        self.docker_cmd = docker_cmd or ['sudo', '-n', 'docker']
        self.user = user or (f'{os.getuid()}:{os.getgid()}' if hasattr(os, 'getuid') else '1000:1000')

    def argv(self, name, work, language, seconds):
        command = ['python', '/work/main.py'] if language == 'python' else ['bash', '/work/main.sh']
        return self.docker_cmd + [
            'run', '--rm', '--name', name, '--user', self.user, '--network', 'bridge',
            '--memory', MEMORY, '--memory-swap', SWAP, '--cpus', CPUS, '--pids-limit', PIDS,
            '--read-only', '--tmpfs', '/tmp:rw,exec,size=256m', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '-v', f'{work}:/work:rw', '-w', '/work', '-e', 'HOME=/work', '-e', 'MPLBACKEND=Agg', '-e', 'PYTHONUNBUFFERED=1',
            '-e', 'PIP_USER=1', '-e', 'PYTHONUSERBASE=/work/.local',
            self.image, 'timeout', '-s', 'KILL', str(seconds)] + command

    def clean(self):
        for folder in self.runs.glob('*') if self.runs.exists() else []:
            try:
                if time.time() - folder.stat().st_mtime > KEEP_SECONDS:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass

    def run(self, args, outbox):
        language = args.get('language', 'python')
        code = args.get('code', '')
        seconds = args.get('timeout', 60)
        if language not in ('python', 'bash'):
            return json.dumps({'error': 'language must be python or bash'})
        if not isinstance(code, str) or not code.strip() or len(code) > 100_000:
            return json.dumps({'error': 'code is empty or over 100,000 characters'})
        if type(seconds) is not int or not 1 <= seconds <= 300:
            return json.dumps({'error': 'timeout must be 1..300 seconds'})
        self.runs.mkdir(parents=True, exist_ok=True)
        self.clean()
        run_id = uuid.uuid4().hex[:12]
        work = self.runs / run_id
        (work / 'out').mkdir(parents=True)
        (work / 'in').mkdir()
        source = Path(outbox) / 'files'
        for name in args.get('inputs') or []:
            path = (source / str(name)).resolve()
            if not path.is_file() or not path.is_relative_to(source.resolve()):
                shutil.rmtree(work, ignore_errors=True)
                return json.dumps({'error': f'input {name!r} is not a file of this request'}, ensure_ascii=False)
            shutil.copy2(path, work / 'in' / path.name)
        (work / ('main.py' if language == 'python' else 'main.sh')).write_text(code, encoding='utf-8')
        name = 'daevis-run-' + run_id
        started, timed_out = time.monotonic(), False
        with (self.runs / '.lock').open('a') as lock:
            if fcntl:
                fcntl.flock(lock, fcntl.LOCK_EX)  # One container at a time on the small host.
            try:
                done = self.docker(self.argv(name, work, language, seconds), seconds + 30)
                output, exit_code = done.stdout or b'', done.returncode
            except subprocess.TimeoutExpired as error:
                timed_out, output, exit_code = True, error.output or b'', None
                try:
                    self.docker(self.docker_cmd + ['kill', name], 30)
                except Exception:
                    pass
        text = output.decode('utf-8', errors='replace')
        if exit_code == 137 and time.monotonic() - started >= seconds:
            timed_out = True
        if len(text) > OUTPUT_LIMIT:
            text = text[:OUTPUT_LIMIT // 2] + '\n…\n' + text[-OUTPUT_LIMIT // 2 + 40:] + '\n[truncated]'
        files, paths, skipped = [], [], []
        delivered = Path(outbox) / 'files'
        for path in sorted((work / 'out').iterdir()):
            if not path.is_file() or path.is_symlink():
                continue
            if len(files) >= MAX_FILES or path.stat().st_size > FILE_LIMIT:
                skipped.append(path.name)
                continue
            delivered.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, delivered / path.name)
            files.append(path.name)
            paths.append(str(path))
        result = {'exit_code': exit_code, 'timed_out': timed_out, 'seconds': round(time.monotonic() - started, 1), 'output': text,
                  'files': files, 'paths': paths}
        if skipped:
            result['skipped_files'] = skipped
        return json.dumps(result, ensure_ascii=False)


def build_sandbox_tools(outbox, env, box=None):
    box = box or Sandbox(env.get('DAEVIS_SANDBOX_RUNS', RUNS), image=env.get('DAEVIS_SANDBOX_IMAGE', IMAGE))
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['code'], 'properties': {
        'code': {'type': 'string', 'description': 'Python 3.12 코드 또는 bash 스크립트'},
        'language': {'type': 'string', 'enum': ['python', 'bash']},
        'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 300},
        'inputs': {'type': 'array', 'items': {'type': 'string'}, 'description': '이번 요청에서 받은 파일 이름(pc_send_file 등). /work/in/에 복사된다'},
    }}
    return [Tool('run_code', '서버의 격리된 Docker 컨테이너에서 코드를 실행한다(인터넷 가능, 메모리 512MB, 최대 300초, pandas·matplotlib·requests·openpyxl·python-pptx 설치됨). '
                             '/work/out/에 쓴 파일은 소유자에게 전송되고 경로는 pc_save_file로 PC에 저장할 수 있다. 출력과 종료 코드를 돌려준다.',
                 schema, lambda args: box.run(args, outbox()))]
