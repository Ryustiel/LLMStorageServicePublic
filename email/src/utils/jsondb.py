
from typing import Dict, List, Self, Optional, Generic, Type, TypeVar, ClassVar

import json, os, pydantic, aiofiles, asyncio

DataModel = TypeVar("DataModel", bound=pydantic.BaseModel)

class JsonDB(Generic[DataModel]):
    """
    JSON based storage solution.
    XXX : Uses Asyncio locks not file locks.
    Could be replaced with an actual database in the future.
    """
    file_locks: ClassVar[Dict[str, asyncio.Lock]] = {}
    
    def __init__(self, path: str, model: Type[DataModel]):
        self.path = path
        self.model: Type[DataModel] = model
        self.data: Optional[DataModel] = None
    
    async def __aenter__(self) -> DataModel:
        """
        Open and Lock the JSON file for reading.
        Returns an instance of the data model.
        """
        # Ensure directory exists
        dirpath = os.path.dirname(self.path)
        if dirpath:
            await asyncio.to_thread(os.makedirs, dirpath, exist_ok=True)
        
        # Create lock for the JSON file
        lock = self.file_locks.setdefault(self.path + ".lock", asyncio.Lock())
        await lock.acquire()
        
        # Read existing data if file exists
        self.data = await self.read()

        return self.data  # Mutable, intended to be mutated within the async block

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        Save the JSON file and release the lock.
        """
        try:
            # Write data to JSON file with indentation
            if self.data is not None:
                payload = await asyncio.to_thread(self.data.model_dump)
                content = await asyncio.to_thread(json.dumps, payload, indent=4, ensure_ascii=False)

                async with aiofiles.open(self.path, "w", encoding="utf-8") as f:
                    await f.write(content)
        finally:
            # Release the lock
            lock = self.file_locks.get(self.path + ".lock")
            if lock:
                lock.release()
            self.data = None

    async def delete(self) -> None:
        """
        Delete the JSON file.
        """
        exists = await asyncio.to_thread(os.path.exists, self.path)
        if exists:
            await asyncio.to_thread(os.remove, self.path)

    async def read(self) -> DataModel:
        """
        Read the JSON file and return an instance of the data model.
        """
        exists = await asyncio.to_thread(os.path.exists, self.path)
        if exists:
            async with aiofiles.open(self.path, "r", encoding="utf-8") as f:
                content = await f.read()
            # Parse JSON off the event loop (CPU-bound)
            data = await asyncio.to_thread(self.model.model_validate_json, content)
            return data
        else:
            return self.model()

    @classmethod
    async def read_file(cls, path: str, model: Type[DataModel]) -> DataModel:
        """
        Read a JSON file and return an instance of the data model.
        """
        if os.path.exists(path):
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return model.model_validate_json(content)
        else:
            # Return new instance if file doesn't exist
            return model()
        