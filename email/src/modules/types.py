"""
Store processed email data.
"""

import pydantic, abc, datetime, asyncio
import utils.storage

from typing import Dict, List, Optional
from utils.jsondb import JsonDB


class AttachmentMetadata(pydantic.BaseModel):
    filename: str = pydantic.Field(..., description="Original filename of the attachment")
    checksum: Optional[str] = pydantic.Field(None, description="Checksum of the attachment for storage reference")
    size_bytes: int = pydantic.Field(..., description="Size of the attachment in bytes")

class EmailMetadata(pydantic.BaseModel):
    sender: str = pydantic.Field(..., description="Email sender address")
    timestamp: datetime.datetime = pydantic.Field(..., description="Email sent timestamp")
    
    def describe(self) -> str:
        desc = ""
        if self.sender:
            desc += f"From: {self.sender}\n"
        if self.timestamp:
            desc += f"Date: {self.timestamp}\n"
        return desc

class Email(pydantic.BaseModel):
    id: str
    metadata: EmailMetadata
    attachments: List[AttachmentMetadata] = pydantic.Field(
        default_factory=list, 
        description="Checksum id of attachments to reference in storage"
    )
    summary: Optional[str] = pydantic.Field(
        default=None, 
        description="AI-generated summary of the email"
    )
    raw_text: str = pydantic.Field(
        ...,
        description="Raw text content of the email for LLM processing"
    )
    
    async def describe(self) -> str:
        if any(att.checksum for att in self.attachments):
            print(f"Waiting for attachments checks : {[att.model_dump() for att in self.attachments]}")
            attachment_summaries = await asyncio.gather(
                *[
                    utils.storage.wait_for_attachment_summary(att.checksum) 
                    for att in self.attachments 
                    if att.checksum
                ], 
                return_exceptions=True
            )

        email_content = self.metadata.describe()
        email_content += f"Body: {self.raw_text}\n"
        i = 0
        for attachment in self.attachments:
            if not attachment.checksum:
                email_content += f"\n\nAttachment {i} : {attachment.filename} (size: {attachment.size_bytes} bytes) (could not be stored).\n"
            else:
                summary = attachment_summaries.pop(0)
                email_content += f"\n\nAttachment {i} : {attachment.filename} (size: {attachment.size_bytes} bytes) (summary: {summary}).\n"
            i += 1
            
        return email_content

class MailboxCacheModel(pydantic.BaseModel):
    emails: Dict[str, Dict[str, Email]] = {}
    
class MailboxCache:
    """Store and retrieve email data so as not to spam inbox services."""
    def __init__(self, cache_path: str):
        """cache_path: Path to the JSON file to use as email cache."""
        self.email_cache = JsonDB(cache_path, MailboxCacheModel)

    async def add_emails_to_cache(self, inbox_id: str, emails: List[Email]):
        """Add or update emails for a specific inbox."""
        async with self.email_cache as cache:
            if inbox_id not in cache.emails:
                cache.emails[inbox_id] = {}
            new_emails = {email.id: email for email in emails}
            cache.emails[inbox_id].update(new_emails)

    async def get_emails(self, inbox_id: str) -> List[Email]:
        """Retrieve all emails for a specific inbox."""
        cache = await self.email_cache.read()
        return list(cache.emails.get(inbox_id, {}).values())

    async def get_email(self, inbox_id: str, email_id: str) -> Optional[Email]:
        """Retrieve a specific email by ID from a specific inbox."""
        cache = await self.email_cache.read()
        return cache.emails.get(inbox_id, {}).get(email_id)


class MailboxInterface(abc.ABC):
    """
    Retrieve emails from a mailbox service.
    If a cache is provided, use it to avoid re-fetching emails.
    """
    def __init__(self, cache: Optional[MailboxCache] = None):
        self.cache = cache

    @abc.abstractmethod
    async def get_emails_since(self, since: datetime.datetime) -> List[Email]:
        """Fetch emails since a specific date."""
        pass
    
    @abc.abstractmethod
    async def get_email(self, email_id: str) -> Optional[Email]:
        """Fetch a specific email by ID."""
        pass
    
    # TODO LATER : Process attachments conditionally, make attached files part of the metadata.
