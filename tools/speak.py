"""Turn Korean text into an .ogg voice file with the Windows voice on this PC."""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import tempfile
from pathlib import Path

SPEECH_SCRIPT = """
Add-Type -AssemblyName System.Speech
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:SPEAK_TEXT))
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'ko-KR' } | Select-Object -First 1
if (-not $voice) { Write-Error 'No Korean voice installed'; exit 1 }
$synth.SelectVoice($voice.VoiceInfo.Name)
$synth.SetOutputToWaveFile($env:SPEAK_OUT)
$synth.Speak($text)
$synth.Dispose()
"""


def speak(text: str, output: Path) -> None:
    if not text.strip():
        raise ValueError("text is empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        wav = Path(folder) / "speech.wav"
        env = {
            **os.environ,
            "SPEAK_TEXT": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "SPEAK_OUT": str(wav),
        }
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SPEECH_SCRIPT],
            check=True,
            env=env,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
             "-ac", "1", "-ar", "48000", "-c:a", "libopus", str(output)],
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--out", type=Path, default=Path("data/voice/reply.ogg"))
    args = parser.parse_args()
    speak(args.text, args.out)
    print(args.out.resolve())


if __name__ == "__main__":
    main()
