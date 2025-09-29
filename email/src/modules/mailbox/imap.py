"""
Retrieve emails from an SMTP server.
"""

import re, pydantic, datetime, hashlib, mimetypes
import imaplib, email.header, email.utils
import modules.types as types, utils.storage

from typing import List, Optional


# Pydantic models for the email result

class EmailAttachment(pydantic.BaseModel):
    """
    Represents an attachment linked to an email.
    """
    filename: str
    content_type: str
    size: Optional[int] = None  # Size in bytes if available
    reference: Optional[str] = None  # URL where the attachment is stored
    content_bytes: Optional[bytes] = None  # Actual attachment content (only populated when include_attachment_bytes=True)

class EmailFetchResult(pydantic.BaseModel):
    """
    Represents the result of an email fetch operation.
    """
    id: str
    subject: str
    from_addr: str
    date: str
    content: str
    attachments: List[EmailAttachment] = []


def parse_email_date(date_str: str) -> datetime.datetime:
    """
    Parse email date string with robust handling of various formats.
    
    Args:
        date_str: Date string from email header
        
    Returns:
        datetime.datetime: Parsed datetime object
    """
    if not date_str:
        return datetime.datetime.now()
    
    # Remove common suffixes that cause parsing issues
    cleaned_date = date_str.strip()
    
    # Remove common timezone suffixes that can cause issues
    # Pattern to match things like " (UTC)", " (GMT)", " (EST)", etc.
    cleaned_date = re.sub(r'\s*\([^)]+\)\s*$', '', cleaned_date)
    
    try:
        # First try using email.utils.parsedate_to_datetime which is more robust
        return email.utils.parsedate_to_datetime(cleaned_date)
    except (ValueError, TypeError):
        pass
    
    # Common email date formats to try
    date_formats = [
        "%a, %d %b %Y %H:%M:%S %z",  # RFC 2822 format
        "%d %b %Y %H:%M:%S %z",      # Without day name
        "%a, %d %b %Y %H:%M:%S",     # Without timezone
        "%d %b %Y %H:%M:%S",         # Without day name and timezone
        "%Y-%m-%d %H:%M:%S %z",      # ISO-like format
        "%Y-%m-%d %H:%M:%S",         # ISO-like without timezone
    ]
    
    for date_format in date_formats:
        try:
            return datetime.datetime.strptime(cleaned_date, date_format)
        except ValueError:
            continue
    
    # If all parsing attempts fail, return current time
    print(f"Warning: Could not parse date '{date_str}', using current time")
    return datetime.datetime.utcnow()
    
    
# The main IMAP connection class
    
class IMAPRawInterface:
    """
    Handles an IMAP email connection. It creates an IMAP client that can be 
    refreshed automatically if the connection is lost, and provides methods 
    to interact with the mailbox.
    """
    def __init__(self, imap_server: str, email_address: str, password: str, port: int = 993):
        """
        Initialize with the email credentials and server details.
        
        Parameters:
            imap_server (str): Domain or IP address of the IMAP server.
            email_address (str): Your email address.
            password (str): Your password.
            port (int): IMAPS port, defaults to 993.
        """
        self.imap_server = imap_server
        self.email_address = email_address
        self.password = password
        self.port = port
        self.__client = self._create_client()
    
    def _create_client(self):
        """
        Creates and logs in an IMAP client with SSL.
        """
        try:
            client = imaplib.IMAP4_SSL(self.imap_server, self.port)
            client.login(self.email_address, self.password)
            return client
        except Exception as e:
            raise Exception("Failed to create client: " + str(e))
    
    @property
    def client(self):
        """
        Test if the client connection is still alive using a NOOP command.
        If it fails, recreate the client.
        """
        try:
            status, _ = self.__client.noop()
            if status != "OK":
                raise Exception("NOOP command failed")
        except Exception as e:
            print("Current client not working, recreating. Reason:", e)
            self.__client = self._create_client()
        return self.__client
    
    def fetch_email(self, email_id: str, include_attachment_bytes: bool = False) -> EmailFetchResult:
        """
        Fetches and parses a specific email by its ID.
        
        Parameters:
            email_id (str): The email ID to fetch.
            include_attachment_bytes (bool): Whether to download attachment content as bytes.
                                           If False, only metadata is retrieved for performance.
            
        Returns:
            EmailFetchResult: Parsed email data.
        """
        client = self.client
        
        # Ensure INBOX is selected
        status, _ = client.select("INBOX")
        if status != "OK":
            raise Exception("Unable to open INBOX")
        
        # Fetch the email by ID (RFC822 returns the full message data)
        status, msg_data = client.fetch(email_id, "(RFC822)")
        if status != "OK":
            raise Exception(f"Failed to fetch email with ID: {email_id}")
        if not msg_data or not msg_data[0]:
            raise Exception(f"No data returned for email ID: {email_id}")
        
        raw_email = msg_data[0][1]
        if not isinstance(raw_email, bytes):
            raise Exception(f"Unexpected email data format: {type(raw_email)}")
        
        msg = email.message_from_bytes(raw_email)
        
        # Decode the subject header
        subject_raw = msg.get("Subject", "")
        subject, encoding = email.header.decode_header(subject_raw)[0]
        if isinstance(subject, bytes):
            subject = subject.decode(encoding if encoding else "utf-8", errors="replace")
        
        from_addr = msg.get("From", "")
        date = msg.get("Date", "")
        content = ""
        attachments: List[EmailAttachment] = []
        
        # Process email parts: if multipart, iterate over parts. Otherwise, process payload.
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                # Check for plain text content
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    charset = part.get_content_charset()
                    part_content = part.get_payload(decode=True)
                    if part_content:
                        if isinstance(part_content, bytes):
                            content = part_content.decode(charset if charset else "utf-8", errors="replace")
                        else:
                            raise Exception(f"Unexpected part content format for text/plain: {type(part_content)}")
                        
                # Check for attachments
                elif "attachment" in content_disposition.lower():
                    filename = part.get_filename()
                    if filename:
                        filename, enc = email.header.decode_header(filename)[0]
                        if isinstance(filename, bytes):
                            filename = filename.decode(enc if enc else "utf-8", errors="replace")
                        elif not isinstance(filename, str):
                            raise ValueError(f"Unexpected filename type: {type(filename)}")
                    else:
                        filename = "UNSPECIFIED"
                    
                    # Only download attachment bytes if requested
                    payload = None
                    size = None
                    content_bytes = None
                    
                    if include_attachment_bytes:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            content_bytes = payload
                            size = len(payload)
                        else:
                            # Handle case where payload is not bytes
                            content_bytes = None
                            size = None
                    else:
                        # Just get the size without downloading the content
                        # We can estimate size from the Content-Length header if available
                        content_length = part.get("Content-Length")
                        if content_length:
                            try:
                                size = int(content_length)
                            except ValueError:
                                size = None
                    
                    attachment = EmailAttachment(
                        filename=filename,
                        content_type=content_type,
                        size=size,
                        reference=None,  # Set to a file path or URL if stored
                        content_bytes=content_bytes
                    )
                    attachments.append(attachment)
                    
        else:  # Not multipart
            charset = msg.get_content_charset()
            part_content = msg.get_payload(decode=True)
            if part_content:
                if isinstance(part_content, bytes):
                    content = part_content.decode(charset if charset else "utf-8", errors="replace")
                else:
                    raise Exception(f"Unexpected part content format for non-multipart: {type(part_content)}")
        
        return EmailFetchResult(
            id=email_id,
            subject=subject,
            from_addr=from_addr,
            date=date,
            content=content,
            attachments=attachments
        )

    def get_attachment(self, email_id: str, attachment_filename: str) -> Optional[bytes]:
        """
        Downloads a specific attachment from an email by its filename.
        
        Parameters:
            email_id (str): The email ID containing the attachment.
            attachment_filename (str): The name of the attachment to download.
            
        Returns:
            Optional[bytes]: The attachment content as bytes, or None if not found.
        """
        client = self.client
        
        # Ensure INBOX is selected
        status, _ = client.select("INBOX")
        if status != "OK":
            raise Exception("Unable to open INBOX")
        
        # Fetch the email by ID
        status, msg_data = client.fetch(email_id, "(RFC822)")
        if status != "OK":
            raise Exception(f"Failed to fetch email with ID: {email_id}")
        if not msg_data or not msg_data[0]:
            raise Exception(f"No data returned for email ID: {email_id}")
        
        raw_email = msg_data[0][1]
        if not isinstance(raw_email, bytes):
            raise Exception(f"Unexpected email data format: {type(raw_email)}")
        
        msg = email.message_from_bytes(raw_email)
        
        # Process email parts to find the specific attachment
        if msg.is_multipart():
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition"))
                
                # Check for attachments
                if "attachment" in content_disposition.lower():
                    filename = part.get_filename()
                    if filename:
                        filename, enc = email.header.decode_header(filename)[0]
                        if isinstance(filename, bytes):
                            filename = filename.decode(enc if enc else "utf-8", errors="replace")
                        elif not isinstance(filename, str):
                            continue  # Skip if we can't decode the filename
                    else:
                        filename = "UNSPECIFIED"
                    
                    # Check if this is the attachment we're looking for
                    if filename == attachment_filename:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            return payload
                        else:
                            return None
        
        return None  # Attachment not found

    def get_email(self, email_id: str, include_attachment_bytes: bool = False) -> EmailFetchResult:
        """
        Gets a specific email by its ID.
        
        Parameters:
            email_id (str): The email ID to retrieve.
            include_attachment_bytes (bool): Whether to download attachment content as bytes.
            
        Returns:
            EmailFetchResult: The requested email.
        """
        return self.fetch_email(email_id, include_attachment_bytes)

    def get_last_email(self, include_attachment_bytes: bool = False) -> EmailFetchResult:
        """
        Connects to the INBOX, retrieves and parses the latest email,
        and returns a Pydantic EmailFetchResult model including any attachment data.
        
        Parameters:
            include_attachment_bytes (bool): Whether to download attachment content as bytes.
        """
        client = self.client
        
        # Open the INBOX folder
        status, _ = client.select("INBOX")
        if status != "OK":
            raise Exception("Unable to open INBOX")
        
        # Search for all email messages in the INBOX.
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise Exception("Error searching for emails in INBOX")
        
        email_ids = data[0].split()
        if not email_ids:
            raise Exception("No emails found in INBOX")
        
        # Get the identifier for the latest email (last in the list)
        latest_email_id = email_ids[-1]
        
        # Use fetch_email to get the email data
        return self.fetch_email(latest_email_id.decode() if isinstance(latest_email_id, bytes) else latest_email_id, include_attachment_bytes)

    def get_emails_since(self, since_date: datetime.datetime, include_attachment_bytes: bool = False) -> List[EmailFetchResult]:
        """
        Retrieves all emails received since the specified datetime.
        
        Parameters:
            since_date (datetime): The datetime to search from.
            include_attachment_bytes (bool): Whether to download attachment content as bytes.
            
        Returns:
            List[EmailFetchResult]: List of emails received since the specified date.
        """
        client = self.client
        
        # Open the INBOX folder
        status, _ = client.select("INBOX")
        if status != "OK":
            raise Exception("Unable to open INBOX")
        
        # Format the date for IMAP search (SINCE uses DD-MMM-YYYY format)
        search_date = since_date.strftime("%d-%b-%Y")
        
        # Search for emails since the specified date
        status, data = client.search(None, f"SINCE {search_date}")
        if status != "OK":
            raise Exception(f"Error searching for emails since {search_date}")
        
        email_ids = data[0].split()
        if not email_ids:
            return []  # No emails found since the specified date
        
        emails = []
        for email_id in email_ids:
            email_id_str = email_id.decode() if isinstance(email_id, bytes) else email_id
            try:
                email_result = self.fetch_email(email_id_str, include_attachment_bytes)
                emails.append(email_result)
            except Exception as e:
                print(f"Warning: Failed to fetch email ID {email_id_str}: {e}")
                continue
        
        return emails


class IMAPInterface(types.MailboxInterface):
    """
    Interface over an IMAP inbox such as the one at my uni.
    """
    def __init__(self, 
        inbox_id: str, 
        imap_server: str, 
        email_address: str, 
        password: str,
        cache: Optional[types.MailboxCache] = None
    ):
        """
        Initialize with the email credentials and server details.
        
        Parameters:
            cache (MailboxCache): The MailboxCache instance to use for caching emails.
            inbox_id (str): Identifier for this inbox in the cache.
            imap_server (str): Domain or IP address of the IMAP server.
            email_address (str): Your email address.
            password (str): Your password.
        """
        self.cache = cache
        self.inbox_id = inbox_id
        self.raw_interface: IMAPRawInterface = IMAPRawInterface(imap_server, email_address, password)
    
    def _email_to_types_email(self, fetched: EmailFetchResult) -> types.Email:
        """
        Convert an EmailFetchResult to a types.Email model.
        """
        email_model = types.Email(
            id=fetched.id,
            metadata=types.EmailMetadata(
                sender=fetched.from_addr,
                timestamp=parse_email_date(fetched.date),
            ),
            attachments=[
                types.AttachmentMetadata(
                    filename=att.filename,
                    checksum=hashlib.sha3_256(att.content_bytes).hexdigest() if att.content_bytes else None,
                    size_bytes=att.size if att.size else 0,
                )
                for att in fetched.attachments
            ],
            raw_text=fetched.content
        )
        return email_model
    
    async def get_email(self, email_id: str) -> types.Email | None:
        """
        Fetch a specific email by its ID from the IMAP server.
        
        Parameters:
            email_id (str): The email ID to retrieve.
            
        Returns:
            types.Email | None: The requested email if found, None otherwise.
        """
        try:
            fetched_email = self.raw_interface.get_email(email_id, include_attachment_bytes=True)
            
            # Convert to types.Email
            email_model = self._email_to_types_email(fetched_email)
            
            # Update the cache with the email if cache is available
            if self.cache:
                await self.cache.add_emails_to_cache(self.inbox_id, [email_model])
            
            # Upload attachments to storage service
            for attachment in fetched_email.attachments:
                if attachment.content_bytes:
                    try:
                        utils.storage.ensure_storage(
                            data=attachment.content_bytes,
                            filename=attachment.filename,
                            mime_type=mimetypes.guess_type(attachment.filename)[0] or "application/octet-stream"
                        )
                    except Exception as e:
                        
                        # XXX : Clear checksum to indicate upload failure
                        file_name = attachment.filename
                        for att in email_model.attachments:
                            if att.filename == file_name:
                                att.checksum = None
                                
                        import logging
                        logging.warning(f"Failed to upload attachment {attachment.filename} of email ID {email_id}: {e}")

            # TODO : Use the cache to determine whether an email has already been processed
            # and to avoid spamming the storage service with exists checks.
            
            return email_model
            
        except Exception as e:
            print(f"Warning: Failed to fetch email ID {email_id}: {e}")
            return None
    
    async def get_emails_since(self, since: datetime.datetime) -> List[types.Email]:
        """
        Fetch new emails from the IMAP server since the specified datetime,
        store them in the cache, and return them.
        
        Parameters:
            since (Optional[datetime]): If provided, only fetch emails received after this datetime.
                                        If None, fetch all emails.
        Returns:
            List[types.Email]: List of new emails fetched.
        """
        fetched_emails = self.raw_interface.get_emails_since(since, include_attachment_bytes=True)
        
        # Convert to types.Email and store in cache
        new_emails: List[types.Email] = []
        for fetched in fetched_emails:
            email_model = self._email_to_types_email(fetched)
            new_emails.append(email_model)
        
        # Update the cache with new emails
        if self.cache:
            await self.cache.add_emails_to_cache(self.inbox_id, new_emails)
            
        # TODO : Use the cache to determine whether an email has already been processed
        # and to avoid spamming the storage service with exists checks.
        # Upload any attachment that was found to the storage service.
        
        return new_emails
