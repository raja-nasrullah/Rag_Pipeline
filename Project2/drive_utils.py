import os
import pickle
import io
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = "credentials.json"


def get_service():
    creds = None
    if os.path.exists("token.pickle"):
        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.pickle", "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)


def download_file(service, file_id, local_folder="data"):
    """Download a single file from Drive by file ID."""
    file = service.files().get(fileId=file_id).execute()
    file_name = file["name"]
    local_path = os.path.join(local_folder, file_name)
    os.makedirs(local_folder, exist_ok=True)

    request = service.files().get_media(fileId=file_id)
    with open(local_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request, chunksize=1024*1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"Progress: {int(status.progress() * 100)}%")
    print(f"Downloaded ✅ {file_name} -> {local_path}")


def download_files_from_folder(service, folder_id, local_folder="data"):
    """Download all files from a Google Drive folder."""
    os.makedirs(local_folder, exist_ok=True)
    query = f"'{folder_id}' in parents and trashed=false"
    response = service.files().list(q=query).execute()
    files = response.get("files", [])

    if not files:
        print("ℹ️ No new files to download.")
        return

    for file in files:
        download_file(service, file["id"], local_folder=local_folder)


def watch_for_changes(service, local_folder="data", saved_token_path="start_page_token.txt"):
    """Watch for changes in Drive and sync locally."""
    if os.path.exists(saved_token_path):
        with open(saved_token_path, "r") as f:
            page_token = f.read().strip()
    else:
        page_token = service.changes().getStartPageToken().execute()["startPageToken"]

    response = service.changes().list(pageToken=page_token, spaces="drive").execute()
    changes = response.get("changes", [])

    if not changes:
        print("ℹ️ No data or no changes in Drive.")
    else:
        for change in changes:
            file_id = change.get("fileId")
            file = change.get("file")

            # Deleted files
            if not file or change.get("removed"):
                file_name = file.get("name") if file else file_id
                local_path = os.path.join(local_folder, file_name)
                if os.path.exists(local_path):
                    os.remove(local_path)
                    print(f"🗑️ File deleted locally: {local_path}")
                else:
                    print(f"🗑️ File deleted from Drive but not found locally: {file_name}")

            # Added or updated files
            else:
                print(f"📂 File added/updated in Drive: {file['name']}")
                download_file(service, file_id, local_folder=local_folder)

    # Save new start page token
    if "newStartPageToken" in response:
        with open(saved_token_path, "w") as f:
            f.write(response["newStartPageToken"])

