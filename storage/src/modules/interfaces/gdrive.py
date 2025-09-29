
import os, pydantic, hashlib, asyncio, json, io
import googleapiclient.discovery, googleapiclient.http, google.auth.credentials, google.oauth2.credentials

from modules.storage import (
    StorageInterface, 
    FileData, 
    FileDataResponse, 
    RawFileData, 
    SearchQuery
)
import utils.jsondb, utils.oauth2

from typing import List, Optional, Dict, Any, Literal, Tuple


class GoogleDriveInterface(StorageInterface):
    """
    Store and retrieve documents from Google Drive.
    """
    
    def __init__(self, 
        oauth_client: utils.oauth2.OAuth2Manager, 
        cache_file: str, 
        drive_folder: str = "LLMDocumentStore",
    ):
        super().__init__(cache_file)
        self.oauth_client = oauth_client
        self.drive_folder = drive_folder
        
        # initialized on get_drive_service
        self._file_metadata_id: Optional[str] = None  # ID of the json file storing metadata
        self._file_folder_id: Optional[str] = None  # ID of the folder containing files in Google Drive
        
        self._credentials: Optional[google.auth.credentials.Credentials] = None
        self._drive_service: Optional[googleapiclient.discovery.Resource] = None
        self._folder_initialized: bool = False

    async def _ensure_folder_structure(self, service: googleapiclient.discovery.Resource):
        """Ensure the folder structure exists in Google Drive, create it if it does not."""
        # Check main folder
        query = f"name='{self.drive_folder}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()  # type: ignore
        files = response.get('files', [])
        if files:
            main_folder_id = files[0]['id']
        else:
            # Create the folder
            file_metadata = {
                'name': self.drive_folder,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()  # type: ignore
            main_folder_id = folder.get('id')
            
        # Check metadata file
        query = f"name='file_metadata.json' and '{main_folder_id}' in parents and trashed=false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()  # type: ignore
        files = response.get('files', [])
        if files:
            self._file_metadata_id = files[0]['id']
            # Download the cache file and load metadata
            request = service.files().get_media(fileId=self._file_metadata_id)  # type: ignore
            fh = io.BytesIO()
            downloader = googleapiclient.http.MediaIoBaseDownload(fh, request)
            # Get JSON content and store into the local cache
            done = False
            while not done:
                status, done = downloader.next_chunk()  # type: ignore
            fh.seek(0)
            content = fh.read().decode('utf-8')
            metadata = json.loads(content)
            async with self.file_cache as db: 
                # Convert dict values to FileData instances
                files_dict = {}
                for checksum, file_dict in metadata.get('files', {}).items():
                    files_dict[checksum] = FileData(**file_dict)
                db.files = files_dict
        else:
            # Create the metadata file
            file_metadata = {
                'name': 'file_metadata.json',
                'parents': [main_folder_id],
                'mimeType': 'application/json'
            }
            # Create an empty json file
            media = googleapiclient.http.MediaIoBaseUpload(io.BytesIO(b'{"files":{}}'), mimetype='application/json')
            metadata_file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()  # type: ignore
            self._file_metadata_id = metadata_file['id']
            async with self.file_cache as db: db.files = {}  # Reset the cache
            
        # Check files folder
        query = f"name='files' and mimeType='application/vnd.google-apps.folder' and '{main_folder_id}' in parents and trashed=false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()  # type: ignore
        files = response.get('files', [])
        if files:
            self._file_folder_id = files[0]['id']
        else:
            # Create the files folder
            file_metadata = {
                'name': 'files',
                'parents': [main_folder_id],
                'mimeType': 'application/vnd.google-apps.folder'
            }
            folder = service.files().create(body=file_metadata, fields='id').execute()  # type: ignore
            self._file_folder_id = folder.get('id')

    async def get_drive_service(self):
        if self._drive_service is None or (self._credentials and not self._credentials.token_state == google.auth.credentials.TokenState.FRESH):
            self._credentials = await self.oauth_client.get_credentials(
                scopes=['https://www.googleapis.com/auth/drive'],
                user_id="USER_ID"
            )
            self._drive_service = googleapiclient.discovery.build("drive", "v3", credentials=self._credentials)
        if not self._folder_initialized:
            await self._ensure_folder_structure(service=self._drive_service)  # type: ignore
            self._folder_initialized = True
        return self._drive_service

    # Untargeted file operations

    async def search_files(self, query: SearchQuery) -> List[FileDataResponse]:
        """This only list the files in the cache for now."""
        service = await self.get_drive_service()
        cache = await self.file_cache.read()
        return [
            FileDataResponse(
                checksum=checksum,
                is_processing=checksum in self.processing_locks and self.processing_locks[checksum].locked(),
                file_data=file_data
            )
            for checksum, file_data in cache.files.items()
        ]

    async def add_file(self, 
        raw_data: RawFileData, 
        ensure_process: Literal["none", "ocr", "summary"] = "none", 
    ) -> FileDataResponse:
        service = await self.get_drive_service()
        
        # 1. Check if the checksum is already in the cache
        cache = await self.file_cache.read()
        checksum = hashlib.sha3_256(raw_data['content']).hexdigest()  # NOTE : ENCODING FUNCTION
        
        # 2. Upload the file to Google Drive
        if checksum not in cache.files: # Upload the file and update the cache
            
            file_metadata = {
                'name': raw_data['name'],
                'parents': [self._file_folder_id],
                'mimeType': raw_data['mime_type']
            }
            media = googleapiclient.http.MediaIoBaseUpload(io.BytesIO(raw_data['content']), mimetype=raw_data['mime_type'])
            uploaded_file = service.files().create(body=file_metadata, media_body=media, fields='id, name, mimeType, size, webViewLink, modifiedTime').execute()  # type: ignore
            
            file_data = FileData(
                file_reference=uploaded_file['id'],
                name=uploaded_file['name'],
                mime_type=uploaded_file['mimeType'],
                size=int(uploaded_file.get('size', 0)),
                modified_time=uploaded_file['modifiedTime'].replace('Z', '+00:00'),
                raw_ocr=None,
                summary=None
            )
            await self.add_file_data(checksum, file_data)
        else:  # Fetch existing metadata
            file_data = cache.files[checksum]
        
        # 3. Ensure processing if needed
        if ensure_process != "none" and checksum not in self.processing_locks:
            asyncio.create_task(
                self.ensure_ocr(
                    checksum=checksum,
                    ensure_process=ensure_process,
                    raw_file_data=raw_data
                )
            )
        await asyncio.sleep(0.1)  # Give some time for the processing to start if needed
        
        # File already exists, return existing metadata
        return FileDataResponse(
            checksum=checksum,
            file_data=file_data,
            is_processing=True if checksum in self.processing_locks and self.processing_locks[checksum].locked() else False
        )
    
    async def add_file_data(self, checksum: str, file_data: FileData):

        service = await self.get_drive_service()

        async with self.file_cache as db:
            db.files[checksum] = file_data
        
        cache = await self.file_cache.read()
        
        service.files().update(  # type: ignore
            fileId=self._file_metadata_id,
            media_body=googleapiclient.http.MediaIoBaseUpload(
                io.BytesIO(cache.model_dump_json().encode('utf-8')),
                mimetype='application/json'
            )
        ).execute()
        
    # File targeted operations
        
    async def update_file_data(self, checksum: str, updates: Dict[str, Any]):
        async with self.file_cache as db:
            if checksum in db.files:
                for key, value in updates.items():
                    if hasattr(db.files[checksum], key):
                        setattr(db.files[checksum], key, value)
                    else:
                        raise ValueError(f"Invalid attribute '{key}' for FileData.")
            else:
                raise ValueError(f"No file with checksum '{checksum}' found in cache.")
        
        cache = await self.file_cache.read()
        service = await self.get_drive_service()
        
        service.files().update(  # type: ignore
            fileId=self._file_metadata_id,
            media_body=googleapiclient.http.MediaIoBaseUpload(
                io.BytesIO(cache.model_dump_json().encode('utf-8')),
                mimetype='application/json'
            )
        ).execute()

    async def delete_file(self, checksum: str):
        
        file_data = await self.get_file_data(checksum)
        # Delete from Google Drive, from cache and local cache
        
    async def download_file(self, checksum: str) -> RawFileData:
        
        service = await self.get_drive_service()
        file_data = await self.get_file_data(checksum)
        
        downloaded = io.BytesIO()
        request = service.files().get_media(fileId=file_data.file_reference)  # type: ignore
        downloader = googleapiclient.http.MediaIoBaseDownload(downloaded, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()

        return {
            "content": downloaded.getvalue(),
            "name": file_data.name,
            "mime_type": file_data.mime_type
        }

    async def download_link(self, checksum: str) -> str:
        
        service = await self.get_drive_service()
        file_data = await self.get_file_data(checksum)
        
        permission = {
            'role': 'reader',
            'type': 'anyone'
        }
        
        try:
            service.permissions().create(  # type: ignore
                fileId=file_data.file_reference,
                body=permission
            ).execute()
            
            # Get the file with webViewLink
            file_info = service.files().get(  # type: ignore
                fileId=file_data.file_reference,
                fields='webViewLink,webContentLink'
            ).execute()
            
            # Return the direct download link if available, otherwise the view link
            share_link = file_info.get('webContentLink') or file_info.get('webViewLink')
            if not share_link:
                raise ValueError("Failed to retrieve share link for file")
            
            return share_link
            
        except Exception as e:
            raise ValueError(f"Failed to create share link: {e}")
