"""Start workflow event consumption when a Django process begins serving requests."""

import asyncio
import logging
import os
import sys
import threading

from django.apps import apps

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_thread = None
_pid = os.getpid()


def _after_fork():
    global _lock, _thread
    _lock = threading.Lock()
    _thread = None


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)


def _run():
    apps.ready_event.wait()
    try:
        from .event_consumer import run_forever

        asyncio.run(run_forever())
    except Exception as exc:
        logger.error('Workflow event consumer stopped (%s); the next request will restart it.', type(exc).__name__)


def ensure_started(sender=None, **kwargs):
    if (
        not os.getenv('PREFECT_API_URL', '').strip()
        or 'test' in sys.argv[1:2]
        or 'pytest' in sys.modules
    ):
        return
    global _thread, _pid
    with _lock:
        pid = os.getpid()
        if _pid == pid and _thread is not None and _thread.is_alive():
            return
        _pid = pid
        _thread = threading.Thread(target=_run, name='workflow-progress-consumer', daemon=True)
        _thread.start()
