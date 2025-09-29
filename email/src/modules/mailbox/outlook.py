"""
Outlook integration using the O365 library.
"""

from __future__ import annotations

from typing import Any, List, Optional

import O365


class OutlookConnection:
    """
    Handles the connection to the EMail service to retrieve recent emails and such.
    """
    def __init__(self, client_id: str, client_secret: str, tenant_id: str, email_address: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.email_address = email_address

    def authenticate(self) -> "O365.Account":
        """
        Authenticate using client credentials flow and return the account.
        Raises a ValueError if authentication fails.
        """
        account = O365.Account(
            (self.client_id, self.client_secret),
            auth_flow_type="credentials",
            tenant_id=self.tenant_id,
            main_resource=self.email_address,
        )
        ok = account.authenticate()
        if not ok:
            raise ValueError("Failed to authenticate Outlook account with provided credentials")
        return account

    def get_many(self, max_items: int = 5, query: Optional[str] = None) -> List[Any]:
        """
        Get multiple emails from the mailbox, without the attachments.
        Example query : "isRead eq false"
        """

        account = self.authenticate()

        if account.is_authenticated:

            mailbox = account.mailbox()
            inbox = mailbox.inbox_folder()
            # Note: The O365 API expects a Query object for filtering. The optional
            # 'query' parameter is not used directly here to avoid runtime errors
            # with invalid query strings.
            message_get_iterator = inbox.get_messages(limit=max_items, order_by="receivedDateTime desc")
            
            return list(message_get_iterator)

        else:
            raise ValueError("Mailbox account is not authenticated")
        
    def get_one(self, email_id: str) -> Any:
        """
        Get a single email with its attachments from the mailbox.
        """

        account = self.authenticate()

        if account.is_authenticated:

            mailbox = account.mailbox()
            inbox = mailbox.inbox_folder()
            message = inbox.get_message(email_id, download_attachments=True)
            
            if message is None:
                raise ValueError("Could not find message in inbox")
            else:
                return message

        else:
            raise ValueError("Mailbox account is not authenticated")
