"""
OAuth API persistent authentication module.
"""

import os, pydantic, hashlib, json
import googleapiclient.discovery, google.auth.exceptions, google.oauth2.credentials, google.auth.credentials
import google_auth_oauthlib.flow, google.auth.transport.requests

import utils.oauth2

from typing import Callable, Dict, List, Optional, Any, Tuple

PROJECT_CREDENTIALS = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "project_id": os.environ["GOOGLE_PROJECT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    }
}

def compute_locid(user_id: str, scopes: Tuple[str, ...]|List[str]) -> str:
    payload = {
        "user_id": user_id,
        "scopes": sorted(list(set(scopes))) # Sort and remove duplicates
    }
    canonical_string = json.dumps(payload, sort_keys=True)
    encoded_string = canonical_string.encode('utf-8')
    hasher = hashlib.sha256()
    hasher.update(encoded_string)
    return hasher.hexdigest()

class RequireLogin(Exception):
    pass
    
class CredentialStore(pydantic.BaseModel):
    credentials: Dict[str, str] = {}

class OAuth2Manager:
    """
    Provide OAuth2 authentication and token management for multiple users.
    """
    def __init__(self, credential_store_file: str = "credentials.json", redirect_uri: str = "http://localhost/authenticate"):
        self.credential_store_file = credential_store_file
        self.redirect_uri = redirect_uri
        
        self.active_credentials: Dict[str, google.oauth2.credentials.Credentials] = {}
        self.credential_store = utils.jsondb.JsonDB(credential_store_file, CredentialStore)
        self.pending_flows: Dict[str, google_auth_oauthlib.flow.InstalledAppFlow] = {}  # Maps locid to flow

    def _refresh_token(self, locid: str) -> Optional[google.oauth2.credentials.Credentials]:
        """Refresh token for given LOCID if possible. Makes changes in place."""
        # LOCID must exist in self.active_credentials
        if locid not in self.active_credentials:
            raise ValueError("Token with given LOCID does not exist.")
        creds = self.active_credentials[locid]
        try:
            creds.refresh(google.auth.transport.requests.Request())
            self.active_credentials[locid] = creds
            return creds
        except google.auth.exceptions.RefreshError as e:
            return None  # Unable to refresh

    async def get_credentials(self, scopes: List[str], user_id: str) -> google.oauth2.credentials.Credentials:
        """
        Provide a credentials object with an active token for the given scopes and user.
        If no valid token is found, attempts refreshing.
        If refreshing is impossible, initiates the OAuth2 flow to obtain one.
        """
        locid = compute_locid(user_id, scopes)
        
        # Check if we have active credentials
        
        if locid in self.active_credentials:
            creds = self.active_credentials[locid]

            if creds.refresh_token and creds.token_state == google.auth.credentials.TokenState.FRESH:
                return creds
            else:
                refreshed = self._refresh_token(locid)
                if refreshed:
                    return refreshed
                else:
                    del self.active_credentials[locid]
                    
        # Try loading from persistent store
        
        store = await self.credential_store.read()
        
        if locid in store.credentials:
            
            creds_json = store.credentials[locid]
            creds_raw = json.loads(creds_json)
            creds = google.oauth2.credentials.Credentials.from_authorized_user_info(creds_raw)
            self.active_credentials[locid] = creds  # type: ignore
            
            if creds.refresh_token and creds.token_state == google.auth.credentials.TokenState.FRESH:
                return creds
            else:
                refreshed = self._refresh_token(locid)
                if refreshed:
                    return refreshed
                else:
                    del self.active_credentials[locid]
                    async with self.credential_store as db:
                        del db.credentials[locid]

        # Authenticate the user from scratch
        
        flow = google_auth_oauthlib.flow.InstalledAppFlow.from_client_config(
            client_config=PROJECT_CREDENTIALS,
            scopes=scopes
        )
        self.pending_flows[locid] = flow
        flow.redirect_uri = self.redirect_uri + "/" + str(locid)
        auth_url, _ = flow.authorization_url(prompt='consent')

        message = f"Please visit this URL to authorize the application:\n{auth_url}"
        raise RequireLogin(message)

    async def add_user_credentials(self, locid: str, authentication_uri: str):
        
        flow = self.pending_flows.get(locid)
        if flow is None:
            raise ValueError("No pending authentication flow for given locid.")

        flow.fetch_token(authorization_response=authentication_uri)
        
        # Save refresh data to the internal database

        async with self.credential_store as db:
            db.credentials[locid] = flow.credentials.to_json()

        self.active_credentials[locid] = flow.credentials  # type: ignore
        del self.pending_flows[locid]
