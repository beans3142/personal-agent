"""Pure, deterministic between-call guards for the isolated Hermes worker.

The token limit uses aggregate *reported* input after API calls. It is not a
prebilling ceiling and cannot stop an in-flight model call. No raw tool payload
is retained in guard state or returned as a stop reason.
"""
import hashlib
import json
import re


_ERROR_STATUSES = {'error', 'failed', 'denied', 'uncertain', 'blocked'}
_METADATA = {'tool_search', 'tool_describe', 'android_screen'}


MAX_ITERATIONS = 20  # A phone search needs ~6 observe/act pairs plus a summary.


def model_failure_reason(response, max_iterations=None):
    if response.get('completed'):return None
    error=str(response.get('error') or '')+' '+str(response.get('failure_reason') or '')
    error=error.lower()
    if any(x in error for x in ('usage_limit_reached','usage limit has been reached','usage limit reached')):
        return 'usage_limit_reached'
    if any(x in error for x in ('authentication','unauthorized','token expired')):return 'model_auth_failed'
    if 'rate limit' in error or 'rate_limit' in error:return 'model_rate_limited'
    calls=response.get('api_calls')
    if max_iterations and type(calls) is int and calls>=max_iterations:return 'iteration_limit'
    return None


def _message(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for field in ('message', 'error', 'text', 'code'):
            if isinstance(value.get(field), str):
                return value[field]
        content = value.get('content')
        if isinstance(content, list):
            return ' '.join(item['text'] for item in content
                            if isinstance(item, dict) and isinstance(item.get('text'), str))
    return ''


def _error(result):
    for _ in range(12):
        if isinstance(result, str):
            text = result.strip()
            prefix, separator, message = text.partition(':')
            status = prefix.casefold()
            if separator and status in {'error', 'denied', 'blocked'}:
                return status, message
            try:
                decoded = json.loads(text)
            except (ValueError, RecursionError):
                return None
            if decoded == result:
                return None
            result = decoded
        elif isinstance(result, dict):
            raw_status = result.get('status')
            status = raw_status.casefold() if isinstance(raw_status, str) else ''
            has_error = result.get('error') not in (None, False, '')
            if status in _ERROR_STATUSES or result.get('isError') is True or has_error:
                message = _message(result.get('error')) if has_error else _message(result)
                if message.strip().casefold().startswith('denied:'):
                    status = 'denied'
                return status or 'error', message
            if 'result' not in result:
                return None
            result = result['result']
        else:
            return None
    # Excessive wrapping is an invalid tool result, rather than a success.
    return 'error', 'tool result nesting limit'


class TurnGuard:
    def __init__(self, max_input_tokens=100_000, max_repeated_errors=3):
        if any(type(value) is not int or value <= 0 for value in (max_input_tokens, max_repeated_errors)):
            raise ValueError('guard limits must be positive integers')
        self.max_input_tokens = max_input_tokens
        self.max_repeated_errors = max_repeated_errors
        self._streaks = {'action': (None, 0), 'metadata': (None, 0)}
        self._reason = None

    def before_step(self, input_tokens):
        if self._reason is None and type(input_tokens) is int and input_tokens >= self.max_input_tokens:
            self._reason = 'input_token_limit'
        return self._reason

    def observe_tool(self, name, result):
        if self._reason is not None:
            return self._reason
        short_name = name.rsplit('__', 1)[-1]
        bucket = 'metadata' if short_name in _METADATA else 'action'
        error = _error(result)
        if error is None:
            if bucket == 'action':
                self._streaks['action'] = (None, 0)
                self._streaks['metadata'] = (None, 0)
            return None
        status, message = error
        if status == 'denied':
            self._reason = 'owner_denied'
            return self._reason
        normalized = re.sub(r'\s+', ' ', message.strip().casefold())
        category = 'snapshot' if short_name == 'android_screen' or any(
            marker in normalized for marker in ('snapshot', 'ui tree', 'fresh screen')) else 'tool'
        signature = (name, category, status, hashlib.sha256(normalized.encode('utf-8')).digest())
        previous, count = self._streaks[bucket]
        count = count + 1 if previous == signature else 1
        self._streaks[bucket] = (signature, count)
        if count >= self.max_repeated_errors:
            self._reason = 'repeated_snapshot_error' if category == 'snapshot' else 'repeated_tool_error'
        return self._reason
