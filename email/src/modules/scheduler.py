"""
Check for emails at scheduled intervals, or wait for events depending on the provider.
"""

import datetime, asyncio

from typing import AsyncGenerator, Dict, List, Literal, Optional
import modules.types as types


async def scheduler_task(
    source: types.MailboxInterface,
    stop_event: asyncio.Event,
    *,
    interval_minutes: int = 60, 
    max_emails_per_check: int = 5
):
    """
    Check for emails at specific time intervals.
    Update the storage with new email data.
    """
    import logging

    interval_seconds = interval_minutes * 60

    while not stop_event.is_set():
        logging.info("Hello from scheduler!")

        # TODO : Trigger fetching emails, summarizing, storing, and various actions such as sending daily digests.

        await asyncio.sleep(interval_seconds)
