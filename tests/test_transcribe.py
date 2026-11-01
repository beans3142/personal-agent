import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "transcribe.py"), *args],
        capture_output=True,
        encoding="utf-8",
    )


def test_missing_path_argument_fails():
    assert run_cli().returncode == 2


def test_missing_file_fails():
    assert run_cli(str(ROOT / "no-such-file.ogg")).returncode != 0


def test_silence_gives_empty_text(tmp_path):
    audio = tmp_path / "silence.ogg"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=16000:cl=mono", "-t", "2", "-c:a", "libopus", str(audio)],
        check=True,
    )
    result = run_cli(str(audio))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_korean_speech_is_transcribed():
    sample = ROOT / "tests" / "fixtures" / "korean-sample.ogg"
    result = run_cli(str(sample))
    assert result.returncode == 0, result.stderr
    assert "안녕" in result.stdout
