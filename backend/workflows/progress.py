"""Cross-process workflow progress notifications and an SSE subscription."""

import json
import select

import psycopg2
from django.db import connection
from rest_framework.renderers import JSONRenderer


class EventStreamRenderer(JSONRenderer):
    """Accept SSE requests before DRF authenticates the streaming action."""

    media_type = 'text/event-stream'
    format = 'event-stream'


def publish_progress(execution):
    """PostgreSQL delivers the notification only if the current transaction commits."""
    payload = json.dumps({
        'execution_id': str(execution.id),
        'workflow_id': str(execution.workflow_id),
    })
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_notify(%s, %s)', ['workflow_progress', payload])


def stream_progress():
    # ponytail: one thread and DB connection per stream; use ASGI/shared LISTEN at scale.
    listener = psycopg2.connect(**connection.get_connection_params())
    try:
        listener.autocommit = True
        with listener.cursor() as cursor:
            cursor.execute('LISTEN workflow_progress')
        # Subscribe before the client fetches its snapshot, including after reconnects.
        yield 'event: ready\ndata: {}\n\n'
        while True:
            listener.poll()
            while listener.notifies:
                notification = listener.notifies.pop(0)
                yield f'event: progress\ndata: {notification.payload}\n\n'
            if not select.select([listener], [], [], 15)[0]:
                yield ': heartbeat\n\n'
    finally:
        listener.close()
