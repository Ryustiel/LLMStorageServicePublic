
import abc, pydantic, datetime, asyncio
import utils.jsondb, modules.ocr

from typing import List, Optional, Dict, TypedDict, Literal, Tuple


class RawFileData(TypedDict):
    content: bytes
    name: str
    mime_type: str

class FileData(pydantic.BaseModel):
    file_reference: str  # A string to find the file back on the storage service
    name: str
    mime_type: str
    size: int  # Size in bytes
    modified_time: str
    raw_ocr: Optional[str] = None
    summary: Optional[str] = None
    
class FileDataResponse(pydantic.BaseModel):
    checksum: str = pydantic.Field(..., description="The checksum of the file as is used in the system as ID.")
    is_processing: bool = pydantic.Field(..., description="Whether an OCR/Summary process is being performed or not.")
    file_data: FileData = pydantic.Field(..., description="Metadata of the file.")
    
class FileDataCache(pydantic.BaseModel):
    files: Dict[str, FileData] = {}  # Keyed by MD5 CHECKSUM

class SearchQuery(pydantic.BaseModel):
    max_results: int = 10
    keywords: List[str] = []
    last_modified_since: Optional[datetime.datetime] = None
    last_modified_before: Optional[datetime.datetime] = None
    around_date: Optional[datetime.datetime] = None

class StorageInterface(abc.ABC):
    """
    A storage agnostic module that provides an interface for document storage and retrieval.
    """
    def __init__(self, cache_file: str = "file_cache.json"):
        self.cache_file = cache_file
        self.file_cache = utils.jsondb.JsonDB(cache_file, FileDataCache)  # TODO : Maybe use a parquet file storage instead?
        self.processing_locks: Dict[str, asyncio.Lock] = {}  # To avoid concurrent processing of the same file
    
    async def ensure_ocr(self, 
        checksum: str, 
        ensure_process: Literal["none", "ocr", "summary"],
        raw_file_data: Optional[RawFileData] = None
    ):
        """
        Update the OCR and/or summary of the file 
        with the given checksum if not available.
        Update or not depending on the ensure_process argument.
        """
        
        if ensure_process in ["ocr", "summary"]:
           
            # Check if the file exists in the cache
            file = await self.file_cache.read()
            if checksum not in file.files:
                raise ValueError(f"No file with checksum '{checksum}' found in cache.")
            
            file_data = file.files[checksum]
            
            if checksum not in self.processing_locks:
                self.processing_locks[checksum] = asyncio.Lock()
            
            # Start processing the file
            await self.processing_locks[checksum].acquire()
            file_data = await self.get_file_data(checksum)
            
            if file_data.raw_ocr is None or (ensure_process == "summary" and file_data.summary is None):
                    
                # Perform OCR or summary as needed
                
                if raw_file_data is None:
                    raw_file_data = await self.download_file(checksum)
                    
                if raw_file_data["mime_type"] == "application/pdf" or raw_file_data["mime_type"].startswith("image/"):
                    mime_type = raw_file_data["mime_type"]
                else:
                    file_data.raw_ocr = f"Cannot perform OCR on file with MIME type '{raw_file_data['mime_type']}'"
                    file_data.summary = file_data.raw_ocr
                    mime_type = None
                    
                if mime_type:
                        
                    if file_data.raw_ocr is None:
                        
                        # Need to perform OCR
                        raw_ocr = await modules.ocr.process_document(
                            file_data=raw_file_data["content"],
                            mime_type=mime_type, 
                            summarize_images=True
                        )
                        file_data.raw_ocr = raw_ocr
                        
                    if ensure_process == "summary" and file_data.summary is None:
                        if file_data.raw_ocr is None:  # This should never happen
                            raise ValueError("Cannot summarize a file without OCR text.")
                        new_summary = await modules.ocr.summarize_document(file_data.raw_ocr)
                        file_data.summary = new_summary
                        
                await self.add_file_data(checksum, file_data)
                
            self.processing_locks[checksum].release()

    async def get_file_data(self, checksum: str, ensure_process: Literal["none", "ocr", "summary"] = "none") -> FileData:
        """Retrieve file metadata from the cache by its checksum."""
        cache = await self.file_cache.read()
        
        if checksum not in cache.files:
            raise ValueError(f"No file with checksum '{checksum}' found in cache.")
        
        if ensure_process != "none":
            await self.ensure_ocr(checksum, ensure_process)
            cache = await self.file_cache.read()  # Reread the cache to get updated data
        
        return cache.files[checksum]
    
    async def file_exists(self, checksum: str) -> bool:
        """Check if a file with the given checksum exists in the cache."""
        cache = await self.file_cache.read()
        return checksum in cache.files
    
    async def synchronize_index(self):
        """
        List all the files from the (remote or not) storage
        and make sure their file ids are registered in the file data cache.
        This is an expensive operation and should only be used in case of big trouble.
        """
        raise NotImplementedError("synchronize_index method not implemented.")

    @abc.abstractmethod
    async def search_files(self, query: SearchQuery) -> List[FileDataResponse]:
        """
        Search for files matching the given query.
        Returns a list of file IDs and a list of file metadata dictionaries.
        """
        pass
    
    @abc.abstractmethod
    async def add_file(self, 
        raw_data: RawFileData, 
        ensure_process: Literal["none", "ocr", "summary"] = "none", 
    ) -> Tuple[str, FileData]:
        """
        Add a new file with the given content, name, and MIME type.
        Returns the metadata of the added file.
        """
        pass

    @abc.abstractmethod
    async def add_file_data(self, checksum: str, file_data: FileData):
        """Update the local and remote cache."""
        pass
    
    @abc.abstractmethod
    async def download_file(self, checksum: str) -> RawFileData:
        """Download the file with the given checksum and return its content, name, and MIME type."""
        pass
    
    @abc.abstractmethod
    async def download_link(self, checksum: str) -> str:
        """Get a shareable link to the file with the given checksum."""
        pass

    @abc.abstractmethod
    async def delete_file(self, checksum: str):
        """Delete the file with the given checksum from storage and the cache."""
        pass
