import os, hashlib, asyncio, json, shutil
from pathlib import Path
from typing import List, Optional, Dict, Any, Literal

from modules.storage import StorageInterface, FileData, FileDataResponse, RawFileData, SearchQuery
import utils.jsondb


class LocalStorageInterface(StorageInterface):
    """
    Store and retrieve documents from local filesystem.
    """
    
    def __init__(self, 
        storage_folder: str, 
        cache_file: str
    ):
        super().__init__(cache_file=cache_file)
        self.storage_folder = Path(storage_folder).resolve()
        self.metadata_file_path = self.storage_folder / "file_metadata.json"
        self.files_folder = self.storage_folder / "files"
        
        self._folder_initialized: bool = False

    async def _ensure_folder_structure(self):
        """Ensure the folder structure exists on local filesystem, create it if it does not."""
        if self._folder_initialized:
            return
            
        # Create main storage folder
        await asyncio.to_thread(os.makedirs, self.storage_folder, exist_ok=True)
        await asyncio.to_thread(os.makedirs, self.files_folder, exist_ok=True)
        if await asyncio.to_thread(os.path.exists, self.metadata_file_path):
            # Load existing metadata into cache
            try:
                async with utils.jsondb.aiofiles.open(self.metadata_file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                metadata = await asyncio.to_thread(json.loads, content)
                async with self.file_cache as db:
                    # Convert dict values to FileData instances
                    files_dict = {}
                    for checksum, file_dict in metadata.get('files', {}).items():
                        files_dict[checksum] = FileData(**file_dict)
                    db.files = files_dict
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                raise ValueError(f"Metadata file is corrupted: {e}")
        else:
            # Create empty metadata file
            async with self.file_cache as db: 
                db.files = {}
            await self._save_metadata()
            
        self._folder_initialized = True

    async def _save_metadata(self):
        """Save current cache to the metadata file."""
        cache = await self.file_cache.read()
        metadata_content = cache.model_dump_json(indent=4)
        
        async with utils.jsondb.aiofiles.open(self.metadata_file_path, "w", encoding="utf-8") as f:
            await f.write(metadata_content)

    # Untargeted file operations

    async def search_files(self, query: SearchQuery) -> List[FileDataResponse]:
        """Search files in the local cache."""
        await self._ensure_folder_structure()
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
        await self._ensure_folder_structure()
        
        # 1. Calculate checksum
        checksum = hashlib.sha3_256(raw_data['content']).hexdigest()
        
        # 2. Check if file already exists in cache
        cache = await self.file_cache.read()
        
        if checksum not in cache.files:
            # Save file to local filesystem
            file_path = self.files_folder / f"{checksum}_{raw_data['name']}"
            
            async with utils.jsondb.aiofiles.open(file_path, "wb") as f:
                await f.write(raw_data['content'])
            
            # Create file metadata
            file_data = FileData(
                file_reference=str(file_path),
                name=raw_data['name'],
                mime_type=raw_data['mime_type'],
                size=len(raw_data['content']),
                modified_time=await asyncio.to_thread(
                    lambda: __import__('datetime').datetime.now().isoformat() + "+00:00"
                ),
                raw_ocr=None,
                summary=None
            )
            
            await self.add_file_data(checksum, file_data)
        else:
            # File already exists, get existing metadata
            file_data = cache.files[checksum]
        
        # 3. Perform OCR and/or summary if needed
        if ensure_process != "none" and checksum not in self.processing_locks:
            asyncio.create_task(
                self.ensure_ocr(
                    checksum=checksum,
                    ensure_process=ensure_process,
                    raw_file_data=raw_data
                )
            )
        await asyncio.sleep(0.1)  # Give some time for the processing to start if needed
        
        return FileDataResponse(
            checksum=checksum,
            file_data=file_data,
            is_processing=True if checksum in self.processing_locks and self.processing_locks[checksum].locked() else False
        )
    
    async def add_file_data(self, checksum: str, file_data: FileData):
        """Add or update file metadata in cache and save to metadata file."""
        async with self.file_cache as db:
            db.files[checksum] = file_data
        
        await self._save_metadata()
        
    # File targeted operations
        
    async def update_file_data(self, checksum: str, updates: Dict[str, Any]):
        """Update specific fields of file metadata."""
        async with self.file_cache as db:
            if checksum in db.files:
                for key, value in updates.items():
                    if hasattr(db.files[checksum], key):
                        setattr(db.files[checksum], key, value)
                    else:
                        raise ValueError(f"Invalid attribute '{key}' for FileData.")
            else:
                raise ValueError(f"No file with checksum '{checksum}' found in cache.")
        
        await self._save_metadata()

    async def delete_file(self, checksum: str):
        """Delete file from local storage and cache."""
        file_data = await self.get_file_data(checksum)
        
        # Delete physical file
        file_path = Path(file_data.file_reference)
        if await asyncio.to_thread(os.path.exists, file_path):
            await asyncio.to_thread(os.remove, file_path)
        
        # Remove from cache
        async with self.file_cache as db:
            if checksum in db.files:
                del db.files[checksum]
        
        await self._save_metadata()

    async def download_file(self, checksum: str) -> RawFileData:
        """Download file content from local storage."""
        file_data = await self.get_file_data(checksum)
        file_path = Path(file_data.file_reference)
        
        if not await asyncio.to_thread(os.path.exists, file_path):
            raise FileNotFoundError(f"File not found at path: {file_path}")
        
        async with utils.jsondb.aiofiles.open(file_path, "rb") as f:
            content = await f.read()
        
        return {
            "content": content,
            "name": file_data.name,
            "mime_type": file_data.mime_type
        }

    async def download_link(self, checksum: str) -> str:
        """Return a placeholder share link since local storage cannot generate actual share links."""
        file_data = await self.get_file_data(checksum)
        
        # Can't generate actual share links for local storage
        return f"local://{file_data.file_reference}"
    