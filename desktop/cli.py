"""Command line for scripts and other agents, using this PC's registration.

  Daevis.exe ask "내일 일정 알려줘" [--json]   one request; exit code 0 when answered
  Daevis.exe history [--limit N] [--json]       recent requests and answers

With --json, `ask` prints one JSON object per line as the request moves
(started, step, approval, answer) so a caller can stream progress; the last
line is the answer. Payment questions are only shown: they are answered in
the app window or on Discord, never from the command line.
"""
import argparse
import json

from desktop import core, settings


class _Silent:
    def speak(self, text, voice, rate):
        pass


def parser():
    p = argparse.ArgumentParser(prog='Daevis', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    ask = sub.add_parser('ask', help='send one request and wait for the answer')
    ask.add_argument('text')
    ask.add_argument('--json', action='store_true')
    ask.add_argument('--thread', help='id of an earlier request to continue that conversation')
    history = sub.add_parser('history', help='recent requests and answers')
    history.add_argument('--limit', type=int, default=20)
    history.add_argument('--json', action='store_true')
    history.add_argument('--search', help='find requests or answers containing this text')
    return p


def run(argv, relay, out):
    args = parser().parse_args(argv)
    if args.command == 'history':
        size = max(1, min(50, args.limit))
        page = relay.history(limit=size, q=args.search) if args.search else relay.history(limit=size)
        if args.json:
            print(json.dumps(page, ensure_ascii=False), file=out)
        else:
            for item in page.get('items', []):
                print(f"[{item.get('status')}] {item.get('text')}\n  {(item.get('reply') or '').splitlines()[0] if item.get('reply') else ''}", file=out)
        return 0
    final = {}

    def emit(event):
        if event['kind'] == 'answer':
            final.update(event)
        if args.json:
            print(json.dumps(event, ensure_ascii=False), file=out, flush=True)
    core.Controller(relay, _Silent(), dict(settings.DEFAULTS, speak_typed=False), emit).request(args.text, 'typed', thread=args.thread)
    if not args.json:
        print(final.get('text', ''), file=out)
    return 0 if final.get('ok') else 1
