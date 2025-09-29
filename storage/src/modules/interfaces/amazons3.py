
import asyncio, hashlib, json, datetime
import aioboto3, botocore.exceptions

from modules.storage import (
    StorageInterface,
    FileData,
    FileDataResponse,
    RawFileData,
    SearchQuery,
)

from typing import List, Optional, Dict, Any, Literal


class AmazonS3Interface(StorageInterface):
    """
    Store and retrieve documents from Amazon S3.
    - Files are stored under: s3://{bucket}/{base_prefix}/files/{checksum}-{original_name}
    - Metadata cache is stored in: s3://{bucket}/{base_prefix}/file_metadata.json
    """

    def __init__(
        self,
        bucket: str,
        cache_file: str,
        base_prefix: str = "LLMDocumentStore",
        region_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        presigned_url_ttl: int = 3600,
    ):
        super().__init__(cache_file)
        self.bucket = bucket
        self.base_prefix = base_prefix.strip("/")
        self._metadata_key = f"{self.base_prefix}/file_metadata.json"
        self._files_prefix = f"{self.base_prefix}/files/"
        self.presigned_url_ttl = presigned_url_ttl

        self._session = aioboto3.Session(
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )

        self._layout_initialized = False
        self._layout_lock = asyncio.Lock()

    async def _ensure_layout(self):
        """
        Ensure the "file_metadata.json" exists in S3 and initialize local cache from it.
        """
        if self._layout_initialized:
            return

        async with self._layout_lock:
            if self._layout_initialized:
                return

            async with self._session.client("s3") as client:  # type: ignore
                try:
                    # Try to fetch the remote metadata file
                    resp = await client.get_object(Bucket=self.bucket, Key=self._metadata_key)
                    content_bytes = await resp["Body"].read()
                    content_text = content_bytes.decode("utf-8") if content_bytes else '{"files":{}}'
                    metadata = json.loads(content_text or '{"files":{}}')

                    async with self.file_cache as db:
                        files_dict: Dict[str, FileData] = {}
                        for checksum, file_dict in metadata.get("files", {}).items():
                            try:
                                files_dict[checksum] = FileData(**file_dict)
                            except Exception:
                                raise ValueError(f"Invalid file metadata for checksum '{checksum}'")
                        db.files = files_dict

                except botocore.exceptions.ClientError as e:
                    code = e.response.get("Error", {}).get("Code")
                    if code in ("NoSuchKey", "NotFound", "404"):
                        # Create an empty metadata file remotely, reset local cache
                        await client.put_object(
                            Bucket=self.bucket,
                            Key=self._metadata_key,
                            Body=b'{"files":{}}',
                            ContentType="application/json",
                        )
                        async with self.file_cache as db:
                            db.files = {}
                    else:
                        raise

                self._layout_initialized = True

    # Untargeted file operations

    async def search_files(self, query: SearchQuery) -> List[FileDataResponse]:
        """
        Lists files according to the local cache.
        Currently ignores the SearchQuery filters.
        """
        await self._ensure_layout()
        cache = await self.file_cache.read()

        return [
            FileDataResponse(
                checksum=checksum,
                is_processing=checksum in self.processing_locks and self.processing_locks[checksum].locked(),
                file_data=file_data,
            )
            for checksum, file_data in cache.files.items()
        ]

    async def add_file(
        self,
        raw_data: RawFileData,
        ensure_process: Literal["none", "ocr", "summary"] = "none",
    ) -> FileDataResponse:
        await self._ensure_layout()
        
        # NOTE : File reference is S3 object key.

        # 1. Compute checksum
        checksum = hashlib.sha3_256(raw_data["content"]).hexdigest()

        # 2. Check cache and upload if new
        cache = await self.file_cache.read()

        if checksum not in cache.files:
            key = f"{self._files_prefix}{checksum}-{raw_data['name']}"
            async with self._session.client("s3") as client:  # type: ignore
                # Upload object
                await client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=raw_data["content"],
                    ContentType=raw_data["mime_type"],
                )
                # Fetch object metadata to fill FileData accurately
                head = await client.head_object(Bucket=self.bucket, Key=key)
                size = int(head.get("ContentLength", len(raw_data["content"])))
                last_modified = head.get("LastModified")

            # Normalize modified time to ISO8601 with timezone
            if isinstance(last_modified, datetime.datetime):
                # Ensure timezone-aware ISO string
                if last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=datetime.timezone.utc)
                modified_iso = last_modified.astimezone(datetime.timezone.utc).isoformat()
            else:
                modified_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

            file_data = FileData(
                file_reference=key,
                name=raw_data["name"],
                mime_type=raw_data["mime_type"],
                size=size,
                modified_time=modified_iso,
                raw_ocr=None,
                summary=None,
            )

            # 3. Persist metadata locally and remotely
            await self.add_file_data(checksum, file_data)
        else:
            file_data = cache.files[checksum]

        # 4. Launch processing in background if requested
        if ensure_process != "none" and checksum not in self.processing_locks:
            asyncio.create_task(
                self.ensure_ocr(
                    checksum=checksum,
                    ensure_process=ensure_process,
                    raw_file_data=raw_data,
                )
            )
        await asyncio.sleep(0.1)  # Allow processing lock to get acquired

        return FileDataResponse(
            checksum=checksum,
            file_data=file_data,
            is_processing=True if checksum in self.processing_locks and self.processing_locks[checksum].locked() else False,
        )

    async def add_file_data(self, checksum: str, file_data: FileData):
        """
        Update the local cache and sync the JSON metadata object to S3.
        """
        await self._ensure_layout()

        # Update local cache
        async with self.file_cache as db:
            db.files[checksum] = file_data

        # Push updated cache to S3
        cache = await self.file_cache.read()
        payload = cache.model_dump_json().encode("utf-8")

        async with self._session.client("s3") as client:  # type: ignore
            await client.put_object(
                Bucket=self.bucket,
                Key=self._metadata_key,
                Body=payload,
                ContentType="application/json",
            )

    # Optional parity method (not abstract) to update only a subset of fields
    async def update_file_data(self, checksum: str, updates: Dict[str, Any]):
        await self._ensure_layout()

        async with self.file_cache as db:
            if checksum in db.files:
                for key, value in updates.items():
                    if hasattr(db.files[checksum], key):
                        setattr(db.files[checksum], key, value)
                    else:
                        raise ValueError(f"Invalid attribute '{key}' for FileData.")
            else:
                raise ValueError(f"No file with checksum '{checksum}' found in cache.")

        # Push updated cache to S3
        cache = await self.file_cache.read()
        payload = cache.model_dump_json().encode("utf-8")

        async with self._session.client("s3") as client:  # type: ignore
            await client.put_object(
                Bucket=self.bucket,
                Key=self._metadata_key,
                Body=payload,
                ContentType="application/json",
            )

    # File targeted operations

    async def download_file(self, checksum: str) -> RawFileData:
        """
        Download file content from S3 based on metadata in the cache.
        """
        await self._ensure_layout()

        file_data = await self.get_file_data(checksum)
        key = file_data.file_reference

        async with self._session.client("s3") as client:  # type: ignore
            resp = await client.get_object(Bucket=self.bucket, Key=key)
            content: bytes = await resp["Body"].read()

        return {
            "content": content,
            "name": file_data.name,
            "mime_type": file_data.mime_type,
        }

    async def download_link(self, checksum: str) -> str:
        """
        Generate a presigned URL for direct download.
        """
        await self._ensure_layout()

        file_data = await self.get_file_data(checksum)
        key = file_data.file_reference

        async with self._session.client("s3") as client:  # type: ignore
            url = await client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                    # Suggest a download filename to the browser
                    "ResponseContentDisposition": f'attachment; filename="{file_data.name}"',
                },
                ExpiresIn=self.presigned_url_ttl,
            )
            return url

    async def delete_file(self, checksum: str):
        """
        Delete the file from S3 and remove metadata from cache and remote JSON.
        """
        await self._ensure_layout()

        file_data = await self.get_file_data(checksum)
        key = file_data.file_reference

        # Delete from S3
        async with self._session.client("s3") as client:  # type: ignore
            await client.delete_object(Bucket=self.bucket, Key=key)

        # Remove from local cache
        async with self.file_cache as db:
            if checksum in db.files:
                del db.files[checksum]

        # Update remote metadata
        cache = await self.file_cache.read()
        payload = cache.model_dump_json().encode("utf-8")
        async with self._session.client("s3") as client:  # type: ignore
            await client.put_object(
                Bucket=self.bucket,
                Key=self._metadata_key,
                Body=payload,
                ContentType="application/json",
            )
            