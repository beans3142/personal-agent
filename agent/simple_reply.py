"""Exact, read-only date/time intents. No model, tool, or cached answer."""
from datetime import datetime, timedelta, timezone
import os
import re

KST = timezone(timedelta(hours=9))
WEEKDAYS = ('월', '화', '수', '목', '금', '토', '일')
TIME = {'지금몇시야', '지금몇시', '몇시야', '현재시간알려줘', '지금시간알려줘'}
DATE = {'오늘날짜알려줘', '오늘날짜', '오늘며칠이야', '오늘몇일이야'}
DAY = {'오늘무슨요일이야', '오늘무슨요일', '오늘요일알려줘'}


def simple_reply(text, *, now=None, enabled=None):
    if enabled is None:
        enabled = os.environ.get('DAEVIS_SIMPLE_REPLIES', 'on').lower() not in {'off', '0', 'false'}
    if not enabled:
        return None
    query = re.sub(r'^(?:대비스|데비스|자비스)[,\s]*', '', text.strip())
    query = re.sub(r'\s+', '', query).rstrip('?.!~')
    if query not in TIME | DATE | DAY:
        return None
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        raise ValueError('now must have a timezone')
    current = current.astimezone(KST)
    weekday = WEEKDAYS[current.weekday()] + '요일'
    if query in TIME:
        period = '오전' if current.hour < 12 else '오후'
        return f'지금 한국 시간으로 {period} {current.hour % 12 or 12}시 {current.minute}분이에요.'
    if query in DAY:
        return f'오늘은 {weekday}이에요.'
    return f'오늘은 {current.year}년 {current.month}월 {current.day}일 {weekday}이에요.'
