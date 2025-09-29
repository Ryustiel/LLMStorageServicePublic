"""
A FastAPI server capable of emitting additional events.
"""

import os, fastapi, pydantic, asyncio, contextlib, datetime
import modules.types as types, modules.scheduler as scheduler
import modules.mailbox.imap, modules.summarizer

from typing import List, Dict, Literal


inbox_cache = types.MailboxCache("data/email_cache.json")

INBOX: Dict[str, types.MailboxInterface] = {
    "source_1": modules.mailbox.imap.IMAPInterface(
        inbox_id="default",
        imap_server=os.environ["UTC_IMAP_SERVER"],
        email_address=os.environ["UTC_EMAIL_ADDRESS"],
        password=os.environ["UTC_EMAIL_PASSWORD"],
    )
}

@contextlib.asynccontextmanager
async def lifespan(app: fastapi.FastAPI):

    stop = asyncio.Event()
    
    print("Starting scheduler task...")

    task = asyncio.create_task(
        scheduler.scheduler_task(
            source=INBOX["source_1"],
            stop_event=stop,
            interval_minutes=1,
        )
    )
    
    yield

    stop.set()  # extra signal in case of unexpected try except loop from poorly designed libraries used in the inbox
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("Scheduler terminated.")

app = fastapi.FastAPI(lifespan=lifespan)

@app.get("/summary/{source}/{email_id}")
async def email_summary(source: Literal["source_1", "source_2"], email_id: str):
    """Fetch one email and get its summary."""
    
    email = await INBOX[source].get_email(email_id=email_id)
    if not email:
        raise fastapi.HTTPException(status_code=404, detail="Email not found")

    email_content = await email.describe()
    email.summary = await modules.summarizer.summarize(raw_input=f"Email: {email_content}")

    return {"status": "success", "email_id": email_id, "summary": email.summary}

@app.get("/todays_emails/{source}", response_model=List[types.Email])
async def todays_emails(source: Literal["source_1", "source_2"]):
    """Fetch today's emails."""
    emails = await INBOX[source].get_emails_since(
        since=datetime.datetime.now() - datetime.timedelta(days=6),
    )
    return emails

@app.get("/daily_digest/{source}")
async def daily_digest(source: Literal["source_1", "source_2"]):
    """Simple daily digest endpoint that returns a hello message."""
    
    emails = await INBOX[source].get_emails_since(
        since=datetime.datetime.now() - datetime.timedelta(days=2),
    )
    raw_input = "\n\n---\n\n".join([await email.describe() for email in emails])
    summary = await modules.summarizer.summarize(raw_input=raw_input)

    return {"message": summary, "status": "success"}
