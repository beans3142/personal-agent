"""Turn a Discord-formatted answer into one short sentence block for text-to-speech."""
import re

EMOJI = re.compile('[\U0001F000-\U0001FAFF☀-➿️]')


def spoken(reply, limit=300):
    text = re.split(r'\n\s*-#\s', '\n' + (reply or ''), maxsplit=1)[0]  # Drop the usage footer.
    text = re.sub(r'\[([^\]]+)\]\((?:https?://)[^)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r'^\s*(?:[-*>]|#{1,6}|\d+\.)\s+', '', line)
        line = line.replace('****', '\0')  # Keep masked phone numbers (010-****-1234).
        line = re.sub(r'\*\*(.+?)\*\*', r'\1', line).replace('__', '').replace('`', '').replace('|', ' ').replace('\0', '****')
        line = EMOJI.sub('', line).strip()
        if line:
            lines.append(line)
    text = re.sub(r'\s+', ' ', ' '.join(lines)).strip()
    if len(text) > limit:
        cut = max(text.rfind(mark, 0, limit) for mark in ('. ', '? ', '! ', '요. ', '다. '))
        text = text[:cut + 1] if cut > limit // 3 else text[:limit]
    return text.strip()
