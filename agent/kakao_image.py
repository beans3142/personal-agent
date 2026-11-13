"""Send one picture to an already verified Kakao room through the native photo picker.

The picture is made the only file in a dedicated album, so the picker's single
thumbnail in that album is provably the requested file. Success requires a new
outgoing photo bubble in the same verified room; anything else is uncertain.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .kakao_watch import PREFIX, appended_messages, read_chat

ALBUM = '/sdcard/Pictures/Daevis'
BUCKET = 'Daevis'
MEDIA = 'content://media/external/images/media'
# One-time Kakao tips that only explain the picker; "OK" changes nothing.
TIPS = {'Show Shared Media', '공유한 미디어 표시'}
# Kakao warns that original resolution keeps EXIF; generated PNGs carry none.
NOTICE = {'Notice', '알림'}


def album_files(device):
    output = device.shell(['content', 'query', '--uri', MEDIA, '--projection', '_data']).output
    return [line.split('_data=', 1)[1].strip() for line in output.splitlines()
            if '_data=' in line and '/Pictures/' + BUCKET + '/' in line]


def scan(device, remote):
    device.shell(['am', 'broadcast', '-a', 'android.intent.action.MEDIA_SCANNER_SCAN_FILE', '-d', 'file://' + remote])


def stage(device, path, sleep=time.sleep):
    """Leave exactly this picture in the album; return its device path."""
    path = Path(path)
    for old in album_files(device):
        device.shell(['rm', '-f', old])
        scan(device, old)
    remote = ALBUM + '/' + path.name
    device.shell(['mkdir', '-p', ALBUM])
    device.push(str(path), remote)
    scan(device, remote)
    for _ in range(20):
        files = album_files(device)
        if len(files) == 1 and files[0].endswith('/Pictures/' + BUCKET + '/' + path.name):
            return remote
        sleep(.5)
    raise ValueError('picture was not indexed alone in the album')


def chat_items(xml, room, members):
    """Bubbles in order, photos included; verifies the room title first."""
    read_chat(xml, room, members)
    items = []
    for bubble in ET.fromstring(xml).iter('node'):
        if bubble.get('resource-id') != PREFIX + 'bubble_root':
            continue
        nodes = list(bubble.iter('node'))
        incoming = any(n.get('resource-id') == PREFIX + 'profile_layout' for n in nodes)
        photo = any(n.get('resource-id') == PREFIX + 'image' for n in nodes)
        texts = [n.get('text') or n.get('content-desc') for n in nodes if n.get('resource-id') == PREFIX + 'message']
        body = '\n'.join(filter(None, texts))
        if photo or body:
            items.append({'incoming': incoming, 'photo': photo, 'text': body})
    return items


class Picker:
    def __init__(self, control, sleep=time.sleep):
        self.c, self.sleep = control, sleep

    def find(self, match, tries=12):
        """Observe until exactly one node matches; return (snapshot, node)."""
        for _ in range(tries):
            observation = self.c.observe()
            found = [n for n in self.c.nodes if match(n)]
            if len(found) == 1:
                return observation['snapshot'], found[0]
            if len(found) > 1:
                raise ValueError('ambiguous picker target')
            self.dismiss_tip()
            self.sleep(.4)
        raise ValueError('picker target not found')

    def tap(self, match, tries=12):
        snapshot, node = self.find(match, tries)
        self.c.click(snapshot, node['id'])
        self.sleep(.6)
        return node

    def titles(self):
        return {n['text'] for n in self.c.nodes if n['resource'] == PREFIX + 'txt_title'}

    def dismiss_tip(self):
        if not self.titles() & TIPS:
            return
        buttons = [n for n in self.c.nodes if n['class'].endswith('Button') and n['text'] in {'OK', '확인'}]
        if len(buttons) == 1:
            self.c.click(self.c.snapshot, buttons[0]['id'])
            self.sleep(.6)
            self.c.observe()


def is_row(text):
    return lambda n: n['resource'] == PREFIX + 'txt_title' and n['text'] == text


def send_image(watcher, state, path, sleep=time.sleep):
    """Pick the album's only picture and send it to the open verified room."""
    control = watcher.control
    remote = stage(control.device.device, path, sleep)
    before = chat_items(control.device.dump_hierarchy(), state['room'], state['members'])
    picker = Picker(control, sleep)
    picker.tap(lambda n: n['description'] in {'Open media keyboard', '미디어 키보드 열기'})
    picker.tap(lambda n: n['text'] in {'Photo', '사진'} and n['resource'] != PREFIX + 'txt_title')
    picker.tap(lambda n: n['resource'] == PREFIX + 'bucket_name')
    _, count = picker.find(lambda n: n['resource'] == PREFIX + 'txt_count' and
                           any(is_row(BUCKET)(t) and abs(t['bounds'][1] - n['bounds'][1]) < 8 for t in control.nodes))
    if count['text'] != '1':
        raise ValueError('album does not hold exactly one picture')
    picker.tap(is_row(BUCKET))
    _, name = picker.find(lambda n: n['resource'] == PREFIX + 'bucket_name')
    counts = [n['text'] for n in control.nodes if n['resource'] == PREFIX + 'bucket_count']
    boxes = [n for n in control.nodes if n['resource'] == PREFIX + 'checkbox_container']
    if name['text'] != BUCKET or counts != ['1'] or len(boxes) != 1:
        raise ValueError('picker is not showing the single staged picture')
    picker.tap(lambda n: n['resource'] == PREFIX + 'checkbox_container')
    # The picker header and the media panel behind it both have Send; use the header one.
    picker.tap(lambda n: n['resource'] == PREFIX + 'button_text' and n['text'] in {'Send', '전송'} and n['enabled']
               and abs((n['bounds'][1] + n['bounds'][3]) - (name['bounds'][1] + name['bounds'][3])) < 120)
    for _ in range(30):
        sleep(.5)
        control.observe()
        if picker.titles() & NOTICE:
            buttons = [n for n in control.nodes if n['class'].endswith('Button') and n['text'] in {'Send', '전송'}]
            if len(buttons) != 1:
                raise RuntimeError('send result is uncertain; do not retry automatically')
            control.click(control.snapshot, buttons[0]['id'])
            continue
        try:
            current = chat_items(control.device.dump_hierarchy(), state['room'], state['members'])
        except ValueError:
            continue
        appended = appended_messages(before, current) if before else current
        if any(item['photo'] and not item['incoming'] for item in appended):
            return remote
    raise RuntimeError('send result is uncertain; do not retry automatically')
