import threading
import time
import uuid
import json
import os
import io

from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError
from pyngrok import ngrok

# ------------------- CONFIG -------------------
CREDENTIALS_FILE = "credentials.json"
PAGE_TOKEN_FILE = "page_token.json"
TARGET_FOLDER_ID = "Drive Folder ID"
SCOPES = [
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
FLASK_PORT = 8080
FILE_MAP = "file_map.json"

# ------------------- AUTH -------------------
credentials = service_account.Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=SCOPES
)
drive_service = build("drive", "v3", credentials=credentials)

# ------------------- PAGE TOKEN UTILS -------------------
def load_page_token():
    if os.path.exists(PAGE_TOKEN_FILE):
        try:
            with open(PAGE_TOKEN_FILE, "r") as f:
                data = json.load(f)
                token = data.get("pageToken")
                if token:
                    print(f"Loaded token: {token}")
                    return token
        except Exception as e:
            print(f"Error loading page token: {e}")

    token = drive_service.changes().getStartPageToken().execute().get("startPageToken")
    save_page_token(token)
    print(f"Saved token: {token}")
    return token


def save_page_token(token):
    try:
        with open(PAGE_TOKEN_FILE, "w") as f:
            json.dump({"pageToken": token}, f)
        print(f"✅ Page token updated to {token}")
    except Exception as e:
        print(f"Error saving page token: {e}")

# ------------------- FILE MAP UTILS -------------------
def load_file_map():
    if os.path.exists(FILE_MAP):
        try:
            with open(FILE_MAP, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading file map: {e}")
    return {}


def save_file_map(file_map):
    try:
        with open(FILE_MAP, "w") as f:
            json.dump(file_map, f)
    except Exception as e:
        print(f"Error saving file map: {e}")

# ------------------- CHECK IF DRIVE FOLDER IS EMPTY -------------------
def is_drive_folder_empty(service, folder_id):
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id)",
            pageSize=1
        ).execute()
        files = results.get('files', [])
        return len(files) == 0
    except Exception as e:
        print(f"Error checking Drive folder emptiness: {e}")
        return False

# ------------------- INITIAL SYNC -------------------
def initial_sync(service, folder_id, local_folder="data", file_map=None):
    query = f"'{folder_id}' in parents and trashed=false"
    page_token = None

    while True:
        response = service.files().list(
            q=query,
            spaces='drive',
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute()

        file_map = file_map or load_file_map()
        os.makedirs(local_folder, exist_ok=True)

        for file in response.get('files', []):
            local_path = os.path.join(local_folder, file['name'])
            if not os.path.exists(local_path):
                print(f"Initial sync: downloading missing file {file['name']}")
                download_file_if_not_exists(service, file, local_folder, file_map=file_map)
            else:
                if file['id'] not in file_map:
                    file_map[file['id']] = file['name']

        save_file_map(file_map)

        page_token = response.get('nextPageToken', None)
        if not page_token:
            break

# ------------------- CLEAR LOCAL FOLDER -------------------
def clear_local_folder(local_folder="data"):
    if not os.path.exists(local_folder):
        return
    try:
        for filename in os.listdir(local_folder):
            file_path = os.path.join(local_folder, filename)
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"Deleted local file: {file_path}")
    except Exception as e:
        print(f"Error clearing local folder: {e}")

# ------------------- FLASK APP -------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Flask running!"

@app.route("/notifications", methods=["POST"])
def notifications():
    print("\nNotification received:")
    print("Headers:", dict(request.headers))
    try:
        print("Body:", request.data.decode("utf-8"))
    except Exception:
        print("Body: (non-UTF8)")

    channel_id = request.headers.get("X-Goog-Channel-ID")
    resource_state = request.headers.get("X-Goog-Resource-State")
    resource_id = request.headers.get("X-Goog-Resource-ID")

    print(f"Notify state={resource_state}, channel={channel_id}, resource={resource_id}")

    page_token = load_page_token()
    try:
        fetch_and_process_changes(drive_service, page_token)  # token saving handled inside
    except Exception as e:
        print(f"Error processing changes: {e}")

    return "", 200

# ------------------- DRIVE WATCH -------------------
def watch_drive_changes(webhook_url):
    channel_id = str(uuid.uuid4())
    page_token = load_page_token()

    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
    }

    response = drive_service.changes().watch(body=body, pageToken=page_token).execute()
    print(f"Watch started: {response}")
    return response

# ------------------- HELPER: DELETE LOCAL FILE -------------------
def delete_local_file(file_id, file_map):
    filename = file_map.get(file_id)
    if filename:
        local_path = os.path.join("data", filename)
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
                print(f"🗑️ Deleted local file: {local_path}")
            except Exception as e:
                print(f"Error deleting local file {local_path}: {e}")
        else:
            print(f"Local file not found for deletion: {local_path}")

        file_map.pop(file_id, None)
        save_file_map(file_map)
    else:
        print(f"No mapping found for deleted fileId: {file_id}")

# ------------------- PROCESS CHANGES -------------------
def fetch_and_process_changes(service, saved_page_token):
    """
    Processes all changes since saved_page_token.
    Uses per-call processed_ids to avoid skipping future change events.
    Pulls file metadata inline with changes().list when possible.
    """
    page_token = saved_page_token
    file_map = load_file_map()
    processed_ids = set()  # per-run set, NOT global (so later changes are still processed)

    while True:
        # Request file metadata inline to avoid extra files().get calls when possible.
        response = service.changes().list(
            pageToken=page_token,
            fields="changes(fileId,removed,file(id,name,mimeType,parents,trashed)),newStartPageToken,nextPageToken",
        ).execute()

        changes = response.get("changes", [])
        if not changes:
            # No changes in this batch. Update tokens if present and break.
            if "newStartPageToken" in response:
                saved_page_token = response["newStartPageToken"]
                save_page_token(saved_page_token)
            break

        for change in changes:
            file_id = change.get("fileId")
            removed = change.get("removed")
            # avoid processing duplicate items inside same API response
            if file_id in processed_ids:
                continue
            processed_ids.add(file_id)

            # permanent deletion (removed=True)
            if removed:
                print(f"🗑️ Permanently deleted: {file_id}")
                delete_local_file(file_id, file_map)
                continue

            # try to use inline 'file' metadata from the change (faster, fewer API calls)
            change_file = change.get("file")
            if change_file:
                # moved to trash?
                if change_file.get("trashed", False):
                    print(f"🚮 Moved to Trash: {change_file.get('name')}")
                    delete_local_file(file_id, file_map)
                    continue

                # moved out of the target folder?
                parents = change_file.get("parents") or []
                if TARGET_FOLDER_ID not in parents:
                    print(f"📤 Moved out of folder: {change_file.get('name')}")
                    delete_local_file(file_id, file_map)
                    continue

                # added/updated in target folder
                print(f"📥 Added/Updated: {change_file.get('name')} ({change_file.get('mimeType')})")
                download_file_if_not_exists(service, change_file, "data", file_map)
                continue

            # fallback: no inline metadata — fetch metadata (handle 404 -> deleted)
            try:
                file = service.files().get(
                    fileId=file_id,
                    fields="id, name, mimeType, trashed, parents"
                ).execute()

                if file.get("trashed", False):
                    print(f"🚮 Moved to Trash (fetched): {file.get('name')}")
                    delete_local_file(file_id, file_map)
                elif TARGET_FOLDER_ID not in (file.get("parents") or []):
                    print(f"📤 Moved out of folder (fetched): {file.get('name')}")
                    delete_local_file(file_id, file_map)
                else:
                    print(f"📥 Added/Updated (fetched): {file.get('name')} ({file.get('mimeType')})")
                    download_file_if_not_exists(service, file, "data", file_map)

            except HttpError as e:
                status = getattr(e, 'status_code', None) or getattr(e, 'resp', {}).get('status') if hasattr(e, 'resp') else None
                err_text = str(e)
                if "404" in err_text or status == 404:
                    print(f"🗑️ Permanently deleted (404): {file_id}")
                    delete_local_file(file_id, file_map)
                else:
                    print(f"⚠️ Error fetching metadata for {file_id}: {e}")
                    # do NOT bail out — continue to next change

        # If API returned newStartPageToken, persist it immediately (we processed up to it)
        if "newStartPageToken" in response:
            saved_page_token = response["newStartPageToken"]
            save_page_token(saved_page_token)

        # Move to next page if provided, otherwise finish
        next_token = response.get("nextPageToken")
        if next_token:
            page_token = next_token
            # continue the while loop to fetch next page
            continue
        else:
            break

    # final save to ensure token persisted
    save_page_token(saved_page_token)
    return saved_page_token

# ------------------- RENEW WATCH -------------------
def renew_watch(webhook_url):
    while True:
        try:
            watch_drive_changes(webhook_url)
        except Exception as e:
            print(f"Watch error: {e}")
        time.sleep(60 * 60 * 6)

# ------------------- DOWNLOAD FILE -------------------
def download_file_if_not_exists(service, file, local_folder="data", file_map=None):
    os.makedirs(local_folder, exist_ok=True)
    local_path = os.path.join(local_folder, file["name"])

    if os.path.exists(local_path):
        print(f"✅ Exists, skipping: {local_path}")
        # ensure map has entry
        if file_map is not None:
            file_map[file["id"]] = file["name"]
            save_file_map(file_map)
        return

    max_retries = 3
    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            request = service.files().get_media(fileId=file["id"])
            # write to a temp file first to avoid corrupt partial files on failure
            tmp_path = local_path + ".part"
            with io.FileIO(tmp_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        print(f"⬇️ Downloading {file['name']}: {int(status.progress() * 100)}%")
            # rename temp to final
            os.replace(tmp_path, local_path)
            print(f"✅ Download complete: {local_path}")

            # Save mapping
            if file_map is not None:
                file_map[file["id"]] = file["name"]
                save_file_map(file_map)
            return

        except HttpError as e:
            err_text = str(e)
            print(f"HttpError downloading {file.get('name')}: {err_text}")
            if "404" in err_text:
                # file no longer exists on Drive
                print(f"🗑️ File not found on Drive during download: {file.get('name')} -> treating as deleted")
                delete_local_file(file["id"], file_map or {})
                return
            # transient; retry
        except Exception as e:
            print(f"Error downloading {file.get('name')} (attempt {attempt}): {e}")
            # possible SSL/network/proxy issue -> retry

        # retry logic
        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2
            print(f"Retrying download (attempt {attempt+1}/{max_retries}) for {file.get('name')}")
        else:
            # final failure
            print(f"Failed to download {file.get('name')} after {max_retries} attempts")
            # cleanup any .part file
            try:
                tmp_path = local_path + ".part"
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return

# ------------------- MAIN -------------------
if __name__ == "__main__":
    print("Checking if Drive folder is empty...")
    if is_drive_folder_empty(drive_service, TARGET_FOLDER_ID):
        print("Drive folder is empty, clearing local folder...")
        clear_local_folder()
        if os.path.exists(FILE_MAP):
            os.remove(FILE_MAP)
            print(f"Deleted file map {FILE_MAP}")
    else:
        print("Drive folder contains files, doing initial sync...")
        initial_sync(drive_service, TARGET_FOLDER_ID, local_folder="data", file_map=load_file_map())

    public_url = ngrok.connect(FLASK_PORT, bind_tls=True).public_url
    print(f"🌐 Public URL: {public_url}")
    WEBHOOK_URL = f"{public_url}/notifications"

    threading.Thread(target=renew_watch, args=(WEBHOOK_URL,), daemon=True).start()

    app.run(host="0.0.0.0", port=FLASK_PORT)
