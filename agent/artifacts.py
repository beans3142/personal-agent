"""Request-scoped artifacts: a late screenshot cannot reach another channel."""
from contextlib import contextmanager,asynccontextmanager
from contextvars import ContextVar
import os
from pathlib import Path

_outbox=ContextVar('daevis_outbox',default=None)


def current_outbox(default):
    return Path(_outbox.get() or os.environ.get('AGENT_OUTBOX') or default)


@contextmanager
def outbox_scope(path):
    token=_outbox.set(Path(path))
    try: yield Path(path)
    finally: _outbox.reset(token)


@asynccontextmanager
async def async_outbox_scope(path):
    with outbox_scope(path): yield Path(path)
