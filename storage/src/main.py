"""
FastAPI Gateway to store, retrieve and process documents.
"""

import os, fastapi, pydantic, tempfile, utils.oauth2
import modules.storage as storage, modules.ocr as ocr
import modules.interfaces.gdrive as gdrive, modules.interfaces.local as localfiles, modules.interfaces.amazons3 as amazons3

from typing import Optional, List, Literal


app = fastapi.FastAPI(title="LLM Document Store API")


OAUTH2 = utils.oauth2.OAuth2Manager(redirect_uri=f"{os.environ['PUBLIC_API_URL']}/auth")
@app.get("/auth/{locid}")
async def authenticate(locid: str, request: fastapi.Request):
    """
    Authenticate endpoint that receives Google OAuth2 redirect
    and passes the full request URL to the OAuth manager.
    This is a GET endpoint as it's the target of a browser redirect.
    """
    try:
        # The google-auth-oauthlib library can parse the full URL, including the host.
        full_redirect_uri = str(request.url)
        
        if full_redirect_uri.startswith("http://"):
            full_redirect_uri = "https" + full_redirect_uri[4:]

        await OAUTH2.add_user_credentials(locid=locid, authentication_uri=full_redirect_uri)
        return {"status": "success", "message": "Authentication successful. You can now close this window."}
    except Exception as e:
        raise fastapi.HTTPException(status_code=400, detail=f"Authentication failed: {e}")


INTERFACES = {
    "gdrive_1": gdrive.GoogleDriveInterface(
        oauth_client=OAUTH2,
        cache_file="./data/file_cache_gdrive_1.json",
        drive_folder="LLMDocumentStore"
    ), 
    "local_1": localfiles.LocalStorageInterface(
        cache_file="./data/file_cache_local_1.json",
        storage_folder="./data/local_storage/"
    ),
    "s3_1": amazons3.AmazonS3Interface(
        cache_file="./data/file_cache_s3_1.json",
        bucket="meep-file-storage",
        base_prefix="LLMDocumentStore",
        region_name="eu-north-1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
}
def get_interface(name: str) -> storage.StorageInterface:
    if name not in INTERFACES:
        raise fastapi.HTTPException(status_code=400, detail=f"Storage interface '{name}' not found.")
    return INTERFACES[name]


@app.post("/upload_file/{storage_interface}", response_model=storage.FileDataResponse)
async def upload_file(
    storage_interface: Literal["s3_1", "local_1", "gdrive_1"],
    file: fastapi.UploadFile,
    ensure_process: Literal["none", "ocr", "summary"] = "none",
):
    """
    Uploads a file to Google Drive if it doesn't already exist.
    Optionally run processing layers on the file after upload.
    Only overwrite and reprocess if the name is the same but the byte count is different.
    """
    try:
        file_content = await file.read()
        file_name = file.filename or "uploaded_file"
        
        # Refuse file if too large or neither pdf nor image
        if not file.content_type:
            raise fastapi.HTTPException(status_code=400, detail="File content type is missing.")
        elif not file.size:
            raise fastapi.HTTPException(status_code=400, detail="File size is missing.")
        
        if file.size > 5 * 1024 * 1024:  # 5 Mb
            raise fastapi.HTTPException(status_code=400, detail="File too large. Maximum allowed size is 5MB.")

        if file.content_type != "application/pdf" and not file.content_type.startswith("image/"):
            raise fastapi.HTTPException(
                status_code=400,
                detail=(
                    f"Invalid file type: {file.content_type}. "
                    "Only PDF and common image types (JPEG, PNG, GIF) are allowed."
                )
            )
        
        # Actually upload the file
        raw_file_data = storage.RawFileData(
            content=file_content,
            name=file_name,
            mime_type=file.content_type
        )

        file_data_response = await get_interface(storage_interface).add_file(
            raw_data=raw_file_data,
            ensure_process=ensure_process
        )
        return file_data_response
    
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File upload failed: {e}")
        

@app.get("/list_files/{storage_interface}", response_model=List[storage.FileDataResponse])
async def list_files(storage_interface: Literal["s3_1", "local_1", "gdrive_1"]):
    """
    Lists all files in the specified storage interface.
    """
    try:
        response = await get_interface(storage_interface).search_files(storage.SearchQuery())
        return response
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File listing failed: {e}")

@app.get("/exists/{storage_interface}/{checksum}")
async def file_exists(storage_interface: Literal["s3_1", "local_1", "gdrive_1"], checksum: str):
    """
    Check if a file with the given checksum exists in the specified storage interface.
    """
    try:
        interface = get_interface(storage_interface)
        exists = await interface.file_exists(checksum)
        return {"exists": exists}
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File existence check failed: {e}")

@app.get("/file/{storage_interface}/{checksum}")
async def get_file(storage_interface: Literal["s3_1", "local_1", "gdrive_1"], checksum: str):
    """
    Generate a google drive share link for this file and give read access to all.
    """
    try:
        return await get_interface(storage_interface).download_link(checksum)
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File download link generation failed: {e}")

@app.post("/data/{storage_interface}/{checksum}")
async def get_file_data(
    storage_interface: Literal["s3_1", "local_1", "gdrive_1"],
    checksum: str, 
    ensure_process: Literal["none", "ocr", "summary"] = fastapi.Query(default="none")
):
    """
    Shows the data from the file with the given ID.
    """
    try:
        file_data = await get_interface(storage_interface).get_file_data(checksum, ensure_process)
        
        # TODO : Process the file if needed (will trigger a cache update) then return updated data
        
        return file_data
        
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File data retrieval failed: {e}")

@app.delete("/file/{storage_interface}/{checksum}")
async def delete_file(storage_interface: Literal["s3_1", "local_1", "gdrive_1"], checksum: str):
    """
    Delete a file with the given checksum from the specified storage interface.
    """
    try:
        await get_interface(storage_interface).delete_file(checksum)
        return {"status": "success", "message": f"File with checksum {checksum} has been deleted."}
    except Exception as e:
        raise fastapi.HTTPException(status_code=500, detail=f"File deletion failed: {e}")
    
@app.get("/test_s3/")
async def test_s3():
    
    # TODO : Move this in its own module
    import aioboto3, botocore.exceptions 
    
    session = aioboto3.Session(
        region_name="eu-north-1",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    )
    async with session.client("s3") as client:  # type: ignore (dynamically generated lib)

        # NOTE : There's one file at s3://meep-file-storage/Compressed Final Image.png

        try:
            paginator = client.get_paginator("list_objects_v2")

            async for page in paginator.paginate(Bucket='meep-file-storage'):
                # The 'Contents' key might be missing if the bucket is empty
                for s3_object in page.get("Contents", []):
                    key = s3_object['Key']
                    size_mb = s3_object['Size'] / (1024 * 1024)
                    last_mod = s3_object['LastModified'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    print(f"- Key: {key}")
                    print(f"  Size: {size_mb:.4f} MB, Last Modified: {last_mod}")

        except botocore.exceptions.ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                print(f"Error: Bucket does not exist.")
            else:
                print(f"An AWS client error occurred: {e}")
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
    