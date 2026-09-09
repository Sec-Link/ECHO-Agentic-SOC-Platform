"""Shared Prefect subscription for the Django runtime and management command."""

import asyncio
import logging
from functools import partial

from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.db import DatabaseError, connection, connections, transaction

from .models import WorkflowEventCheckpoint
from .prefect_events import CHECKPOINT, EVENT_NAMES, consume_event, replay_events
from .worker_credentials import bootstrap_worker_credentials

logger = logging.getLogger(__name__)
CONSUMER_LOCK = 90817264


def _acquire_leadership():
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [CONSUMER_LOCK])
        return connection.connection if cursor.fetchone()[0] else None


def _guarded_call(leader, callback, *args, **kwargs):
    # The same physical session owns the lock and commits event/checkpoint writes.
    # A reconnected session must win leadership before it may process more events.
    with transaction.atomic():
        with connection.cursor() as cursor:
            if connection.connection is not leader:
                raise DatabaseError('The Prefect consumer database session changed.')
            cursor.execute('SELECT 1')
        return callback(*args, **kwargs)


async def run_forever(*, reset_checkpoint=False):
    # Each embedded instance gets its own ORM thread and lock-owning connection.
    async with ThreadSensitiveContext():
        while True:
            try:
                leader = await sync_to_async(_acquire_leadership)()
                if leader is not None:
                    if reset_checkpoint:
                        await sync_to_async(_guarded_call)(
                            leader, lambda: WorkflowEventCheckpoint.objects.filter(pk=CHECKPOINT).delete(),
                        )
                        reset_checkpoint = False
                    logger.info('Prefect workflow progress consumer is active.')
                    await consume(leader)
            except Exception as exc:
                logger.error('Prefect consumer unavailable (%s); retrying in 3 seconds.', type(exc).__name__)
            finally:
                # Closing the session releases its advisory lock, including on cancellation.
                await sync_to_async(connections.close_all)()
            await asyncio.sleep(3)


async def consume(leader):
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(provision_worker())
        tasks.create_task(watch_connection(leader))
        await subscribe(leader)


async def provision_worker():
    def provision():
        try:
            bootstrap_worker_credentials()
        finally:
            connections.close_all()

    while True:
        try:
            # Secret network calls must not occupy the event consumer's ORM thread.
            await sync_to_async(provision, thread_sensitive=False)()
            return
        except Exception as exc:
            logger.error('Worker credential setup failed (%s); retrying in 3 seconds.', type(exc).__name__)
            await asyncio.sleep(3)


async def watch_connection(leader):
    while True:
        await asyncio.sleep(10)
        await sync_to_async(_guarded_call)(leader, lambda: None)


async def subscribe(leader):
    from prefect.events.clients import get_events_subscriber
    from prefect.events.filters import EventFilter, EventNameFilter

    handle_event = partial(_guarded_call, leader, consume_event)
    while True:
        try:
            async with get_events_subscriber(
                filter=EventFilter(event=EventNameFilter(name=EVENT_NAMES)),
                reconnection_attempts=0,
            ) as subscriber:
                # Subscribe before replaying the durable cursor after every disconnect.
                await sync_to_async(replay_events)(handler=handle_event)
                async for event in subscriber:
                    await sync_to_async(handle_event)(event)
        except DatabaseError:
            # Stop this subscription; a fresh connection must acquire the lock again.
            raise
        except Exception as exc:
            logger.error('Prefect event consumer disconnected (%s); retrying in 3 seconds.', type(exc).__name__)
        await asyncio.sleep(3)
