import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from speak import speak
from transcribe import transcribe


def test_empty_text_is_rejected(tmp_path):
    try:
        speak("   ", tmp_path / "x.ogg")
    except ValueError:
        return
    raise AssertionError("empty text must fail")


def test_output_folder_is_created_and_file_is_opus(tmp_path):
    out = tmp_path / "nested" / "reply.ogg"
    speak("테스트 음성입니다", out)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
         "-of", "csv=p=0", str(out)],
        capture_output=True, encoding="utf-8", check=True,
    )
    assert probe.stdout.strip() == "opus"


def test_spoken_text_round_trips_through_transcription(tmp_path):
    out = tmp_path / "reply.ogg"
    speak("안녕하세요 반갑습니다", out)
    assert "안녕" in transcribe(str(out))


def test_special_characters_do_not_break_script(tmp_path):
    out = tmp_path / "reply.ogg"
    speak("가격은 $100 'quote' \"double\" `tick` 입니다", out)
    assert out.stat().st_size > 0
