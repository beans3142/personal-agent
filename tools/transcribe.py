"""Print the text of a local audio file. Runs on this PC with faster-whisper."""

from __future__ import annotations

import argparse
import sys


def transcribe(path: str, model_name: str = "small", language: str = "ko") -> str:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(path, language=language, vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="ko")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    print(transcribe(args.path, args.model, args.language))


if __name__ == "__main__":
    main()
