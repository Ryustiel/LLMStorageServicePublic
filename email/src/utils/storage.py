
import os, requests, hashlib, datetime, asyncio

STORAGE_URL = os.environ["STORAGE_API_URL"]

# TODO : Make it async using httpx


def ensure_storage(data: bytes, filename: str, mime_type: str):
    """
    Check if a file exists in the storage service.
    If it does not, send it to the storage.
    """

    checksum = hashlib.sha3_256(data).hexdigest()

    exists_url = f"{STORAGE_URL}/exists/s3_1/{checksum}"
    try:
        exists_resp = requests.get(exists_url, timeout=10)
        exists_resp.raise_for_status()
        exists_payload = exists_resp.json()
        exists = bool(exists_payload.get("exists"))
    except requests.RequestException as exc:
        raise RuntimeError("Failed to check file existence in storage service.") from exc
    except ValueError as exc:
        raise RuntimeError("Invalid response while checking file existence.") from exc

    if not exists:

        upload_url = f"{STORAGE_URL}/upload_file/s3_1"
        files = {"file": (filename, data, mime_type or "application/octet-stream")}

        try:
            upload_resp = requests.post(
                upload_url,
                params={"ensure_process": "summary"},
                files=files,
                timeout=30,
            )
            upload_resp.raise_for_status()
            upload_payload = upload_resp.json()
        except requests.RequestException as exc:
            raise RuntimeError("Failed to upload file to storage service.") from exc
        except ValueError as exc:
            raise RuntimeError("Invalid response payload received from storage service.") from exc

def get_from_storage(checksum: str) -> dict:
    """
    Retrieve metadata about a file from the storage service using its checksum.
    """

    ensure_url = f"{STORAGE_URL}/data/s3_1/{checksum}"
    try:
        ensure_resp = requests.post(
            ensure_url,
            params={"ensure_process": "summary"},
            timeout=30,
        )
        ensure_resp.raise_for_status()
        return ensure_resp.json()
    
    except requests.RequestException as exc:
        raise RuntimeError("Failed to ensure summary for existing file in storage service.") from exc
    
async def wait_for_attachment_summary(checksum: str, timeout_seconds: int = 20) -> str:
    """
    Wait for an attachment to be processed and available in storage.
    """
    # Try to get the file and see if it exists
    metadata = get_from_storage(checksum)
    if not metadata.get("file_reference"):
        raise RuntimeError("Attachment does not exist in storage.")
    if metadata.get("summary"):
        return metadata["summary"]
    
    start_time = datetime.datetime.now()
    while (datetime.datetime.now() - start_time).total_seconds() < timeout_seconds:
        try:
            metadata = get_from_storage(checksum)
            if metadata.get("summary"):
                return metadata["summary"]
        except RuntimeError:
            await asyncio.sleep(2)
    raise TimeoutError(f"Attachment with checksum {checksum} not available in storage after {timeout_seconds} seconds.")
    