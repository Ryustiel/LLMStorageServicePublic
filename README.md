# Automated Email & Document Processing Pipeline

An asynchronous, multi-service system designed to fetch emails, process attachments with OCR and AI, and generate intelligent summaries. It leverages a pluggable storage backend, allowing you to store and manage documents on local disk, Google Drive, or Amazon S3.

---

## TLDR

-   **Automated Email Ingestion**: Connects to IMAP inboxes to periodically fetch new emails and their attachments.
-   **Intelligent Document Processing**: Extracts text from PDFs and images using Mistral OCR and OpenAI Vision, even describing images within documents.
-   **Pluggable, Content-Addressable Storage**: A dedicated `storage` service handles files, using checksums to prevent duplicates. It supports multiple backends (Local, S3, G-Drive) through a unified API.
-   **AI-Powered Summarization**: Uses OpenAI's GPT models to generate summaries for individual emails (including attachments) or create daily digests.
-   **Asynchronous & Containerized**: Built with FastAPI, Pydantic, and `asyncio` for high performance. The entire stack is orchestrated with Docker Compose for easy setup and later upgrade to server environments.
-   **Robust Caching**: Both services use a custom async-safe JSON "database" for persistent caching of emails and file metadata, preventing redundant processing and API calls. This cache can be later implemented using more robust systems like redis.

---

## Tech Stack and Techniques

-   **Backend Services & Orchestration**
    -   **FastAPI**: For high-performance, async REST APIs in both `email` and `storage` services.
    -   **Docker & Docker Compose**: For containerizing the services and managing the application stack.
    -   **Uvicorn**: As the ASGI server running the FastAPI applications.
-   **LLM & AI Orchestration**
    -   **Langchain**: Used with `langchain-openai` to invoke OpenAI models (`gpt-5-mini`) for summarization tasks. It also provides a very convenient logging / error flagging system for LLM applications.
    -   **Mistral AI API**: Leveraged for high-fidelity OCR on PDF documents.
    -   **OpenAI Vision API**: To generate textual descriptions of images, including images found within documents, which are then integrated into the OCR output.
-   **Storage & Data Management**
    -   **Abstract Storage Layer**: A `StorageInterface` defines cache management and enfore the required API gateway methods, with concrete implementations for Local Filesystem, Google Drive, and Amazon S3.
    -   **Storage Backend Clients**: `aioboto3` for async S3 operations, and `google-api-python-client` for Google Drive.
    -   **Checksum-Based Deduplication**: Files are identified by their `sha3-256` checksum, providing a content-addressable storage system that naturally avoids duplicates.
    -   **Async JSON "Database"**: A custom `JsonDB` utility built on `aiofiles` and `asyncio.Lock` provides a simple, race-condition-free way to persist metadata.
-   **Email Handling & Python Ecosystem**
    -   **IMAP Client**: Uses Python's built-in `imaplib` for robust connection to IMAP servers.
    -   **Pydantic**: Heavily used for data modeling, validation, and settings management across all services. There are some rare cases where a simple dataclass would have been enough.
    -   **uv**: The project is configured to use `uv` for fast dependency installation within the Docker builds.

---

## Highlights

-   **Modular, Multi-Backend Storage**
    The `storage` service features a clean `StorageInterface` abstract base class, making it easy to add new file storage services. Ready-to-use implementations for local disk, Google Drive, and Amazon S3 are included.

-   **Asynchronous Document Processing Pipeline**
    When a file is uploaded, OCR and summarization can be triggered as background tasks (`asyncio.create_task`). The API responds instantly, while processing happens asynchronously, with the status reflected in subsequent API calls.

-   **Checksum-Based Content-Addressable Storage**
    Files are identified and retrieved using their `sha3-256` hash. The `email` service first queries the `storage` service with a checksum to check if an attachment already exists, avoiding redundant uploads.

-   **Rich Document Intelligence**
    The system doesn't just extract text. It identifies images embedded within PDFs, sends them to the OpenAI Vision API for a detailed description, and seamlessly integrates these descriptions back into the document's final markdown representation.

-   **Robust Caching and State Management**
    Both services rely on a custom `JsonDB` utility for managing state (email cache, file metadata). This lightweight solution uses `asyncio` locks to prevent race conditions during file I/O, ensuring data consistency in a concurrent environment. That utility can be easily overriden with any suitable cache database for production.

-   **Interactive OAuth2 for a Backend Service (Google Drive only)**
    The Google Drive interface handles OAuth2 by raising a `RequireLogin` exception containing an authorization URL. A user must visit this URL to grant consent to their google drive folder, and the redirect is caught by a dedicated API endpoint to complete the authentication flow. This is a custom made pattern to connect and store files on my own google drive. Please use cloud storage services like GCS or S3 (already supported) if you intend to use my code.

---

## Installation

### Prerequisites

-   Docker and Docker Compose
-   Git
-   API Keys and Credentials:
    -   OpenAI API Key
    -   Mistral API Key
    -   Google Cloud Project credentials (`client_id`, `client_secret`)
    -   AWS credentials (`access_key_id`, `secret_access_key`)
    -   IMAP server credentials (if using an IMAP server like my demo)

### Clone and Set Up

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```

2.  **Configure Environment Variables:**
    The project uses `.env` files located in the `.environ/` directory. Here is an overview of the required environment in `storage.env` and `email.env`.

    -   `.environ/storage.env` should contain the LLM and storage related credentials:
        ```dotenv
        GOOGLE_CLIENT_ID="..."
        GOOGLE_CLIENT_SECRET="..."
        GOOGLE_PROJECT_ID="..."
        MISTRAL_API_KEY="..."
        OPENAI_API_KEY="..."
        AWS_ACCESS_KEY_ID="..."
        AWS_SECRET_ACCESS_KEY="..."
        AWS_DEFAULT_REGION="eu-north-1"
        PUBLIC_API_URL="http://localhost:8000"
        ```

    -   `.environ/email.env` should look like this:
        ```dotenv
        OPENAI_API_KEY="..."
        STORAGE_API_URL="http://storage:8000"
        # Email services credentials and urls
        ```
    *Note: `STORAGE_API_URL` uses the Docker network service name (`storage`), not `localhost`.*

3. **Choose what to do with the email service:**

    You have two options: run the complete stack including the email service, or run only the storage service.

    If you want to try out the email service and have an IMAP-enabled inbox, add your credentials to `.environ/email.env`:
    ```dotenv
    # .environ/email.env
    OPENAI_API_KEY="..."
    STORAGE_API_URL="http://storage:8000"

    # Add your IMAP credentials below
    UTC_EMAIL_ADDRESS=your_email@example.com
    UTC_EMAIL_PASSWORD=your_password
    UTC_IMAP_SERVER=imap.example.com
    ```

    If you don't have IMAP credentials you can still run the `storage` service by itself using:
        
    ```bash
    docker-compose up --build -d storage
    ```

### Run the Application

Build and run the services using Docker Compose:

```bash
docker-compose up --build -d
```

The services will be available at:
-   **Storage Service**: `http://localhost:8000/docs`
-   **Email Service**: `http://localhost:8001/docs`

To stop the services:
```bash
docker-compose down
```

---

## Project Structure

```text
project-root/
├─ .gitignore
├─ docker-compose.yml           # Orchestrates the `email` and `storage` services.
├─ pyproject.toml               # This is a "dev" environment and will be ignored by docker.
├─ .environ/
│  ├─ email.env                 # Environment variables for the email service.
│  └─ storage.env               # Environment variables for the storage service.
├─ email/
│  ├─ Dockerfile                # Container definition for the email service.
│  ├─ pyproject.toml            # Python dependencies for the email service.
│  └─ src/
│     ├─ main.py                # FastAPI entry point: API endpoints for email summarization.
│     ├─ modules/
│     │  ├─ scheduler.py        # Background task to periodically fetch emails.
│     │  ├─ summarizer.py       # LLM call orchestration for creating summaries.
│     │  ├─ types.py            # Pydantic models for emails, attachments, and mailbox cache.
│     │  └─ mailbox/
│     │     ├─ imap.py          # IMAP email fetching implementation.
│     │     └─ outlook.py       # O365/Outlook email fetching implementation (partially implemented).
│     └─ utils/
│        ├─ jsondb.py           # Async JSON "DB" with file locking.
│        └─ storage.py          # Client for communicating with the storage service.
└─ storage/
   ├─ Dockerfile                # Container definition for the storage service.
   ├─ pyproject.toml            # Python dependencies for the storage service.
   └─ src/
      ├─ main.py                # FastAPI entry point: API for file upload, retrieval, and processing.
      ├─ modules/
      │  ├─ storage.py          # Abstract `StorageInterface` and core data models.
      │  ├─ ocr.py              # Document processing with Mistral OCR and OpenAI Vision.
      │  └─ interfaces/
      │     ├─ local.py         # Local filesystem storage implementation.
      │     ├─ gdrive.py        # Google Drive storage implementation.
      │     └─ amazons3.py      # Amazon S3 storage implementation.
      └─ utils/
         ├─ jsondb.py           # Shared async JSON "DB" utility.
         └─ oauth2.py           # Google OAuth2 credential management.
```
