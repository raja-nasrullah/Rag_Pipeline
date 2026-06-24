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

# 🔗 Import RAG functions
from RAG_Pipleline import embed_and_store, collection

# ------------------- CONFIG -------------------
CREDENTIALS_FILE = "credentials.json"
PAGE_TOKEN_FILE = "page_token.json"
TARGET_FOLDER_ID = "Drive Folder Id"  # Replace with your target folder ID
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

        # 🗑️ Clear embeddings too (FIXED: ChromaDB rejects where={})
        try:
            existing = collection.get()
            existing_ids = existing.get("ids") or []
            if existing_ids:
                collection.delete(ids=existing_ids)
                print(f"🗑️ Cleared {len(existing_ids)} embeddings from ChromaDB")
            else:
                print("ℹ️ No embeddings to clear")
        except Exception as e:
            print(f"Warning clearing embeddings: {e} (continuing)")
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

# ------------------- HELPERS FOR EMBEDDING DELETION -------------------
def _flatten_ids_from_collection_get(resp):
    # safe flatten for various shapes
    ids = resp.get("ids") or []
    flat = []
    for it in ids:
        if isinstance(it, list):
            flat.extend(it)
        else:
            flat.append(it)
    return flat

def delete_embeddings_for_filename(filename):
    """
    Delete all embeddings in ChromaDB that belong to the given filename.
    """
    try:
        collection.delete(where={"source": filename})
        print(f"🗑️ Deleted embeddings for {filename}")
    except Exception as e:
        print(f"❌ Error deleting embeddings for {filename}: {e}")

# ------------------- HELPER: DELETE LOCAL FILE -------------------
def delete_local_file(file_id, file_map):
    filename = file_map.get(file_id)
    if filename:
        local_path = os.path.join("data", filename)

        if os.path.exists(local_path):
            os.remove(local_path)
            print(f"🗑️ Deleted local file: {local_path}")
        else:
            print(f"⚠️ Local file not found: {local_path}")

        # Now delete embeddings from DB
        delete_embeddings_for_filename(filename)

        file_map.pop(file_id, None)
        save_file_map(file_map)
    else:
        print(f"⚠️ No mapping found for deleted fileId: {file_id}")

# ------------------- PROCESS CHANGES -------------------
def fetch_and_process_changes(service, saved_page_token):
    page_token = saved_page_token
    file_map = load_file_map()
    processed_ids = set()

    while True:
        response = service.changes().list(
            pageToken=page_token,
            fields="changes(fileId,removed,file(id,name,mimeType,parents,trashed)),newStartPageToken,nextPageToken",
        ).execute()

        changes = response.get("changes", [])
        if not changes:
            if "newStartPageToken" in response:
                saved_page_token = response["newStartPageToken"]
                save_page_token(saved_page_token)
            break

        for change in changes:
            file_id = change.get("fileId")
            removed = change.get("removed")
            if file_id in processed_ids:
                continue
            processed_ids.add(file_id)

            # permanent deletion (removed=True)
            if removed:
                print(f"🗑️ Permanently deleted: {file_id}")
                delete_local_file(file_id, file_map)
                continue

            # try to use inline 'file' metadata from the change
            change_file = change.get("file")
            if change_file:
                # ----- Confirm trashed before deleting -------
                if change_file.get("trashed", False):
                    # Confirm with an explicit metadata fetch before deleting
                    try:
                        meta = service.files().get(fileId=file_id, fields="trashed,name").execute()
                        if meta.get("trashed", False):
                            print(f"🚮 Confirmed trashed: {meta.get('name')}")
                            delete_local_file(file_id, file_map)
                        else:
                            print(f"⚠️ Inline said trashed but file metadata shows not trashed: {meta.get('name')}. Skipping deletion.")
                        continue
                    except HttpError as e:
                        # If it's a 404 then treat as deleted
                        err_text = str(e)
                        if "404" in err_text:
                            print(f"🗑️ Permanently deleted (404) during confirm: {file_id}")
                            delete_local_file(file_id, file_map)
                            continue
                        else:
                            print(f"⚠️ Error confirming trashed status for {file_id}: {e}. Skipping.")
                            continue

                # ----- Confirm parents before deleting (moved out) -------
                parents = change_file.get("parents") or []
                if TARGET_FOLDER_ID not in parents:
                    # double-check current parents
                    try:
                        meta = service.files().get(fileId=file_id, fields="parents,name").execute()
                        if TARGET_FOLDER_ID not in (meta.get("parents") or []):
                            print(f"📤 Confirmed moved out of folder: {meta.get('name')}")
                            delete_local_file(file_id, file_map)
                        else:
                            print(f"⚠️ Inline said moved out but metadata shows still in folder: {meta.get('name')}. Skipping deletion.")
                        continue
                    except HttpError as e:
                        err_text = str(e)
                        if "404" in err_text:
                            print(f"🗑️ Permanently deleted (404) during parent confirm: {file_id}")
                            delete_local_file(file_id, file_map)
                            continue
                        else:
                            print(f"⚠️ Error confirming parents for {file_id}: {e}. Skipping.")
                            continue

                # ----- If we reach here, it's added/updated in the folder -----
                print(f"📥 Added/Updated: {change_file.get('name')} ({change_file.get('mimeType')})")
                # download + embed (embed_and_store will be called inside download)
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

        # If API returned newStartPageToken, persist it immediately
        if "newStartPageToken" in response:
            saved_page_token = response["newStartPageToken"]
            save_page_token(saved_page_token)

        # Move to next page if provided, otherwise finish
        next_token = response.get("nextPageToken")
        if next_token:
            page_token = next_token
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
        if file_map is not None:
            file_map[file["id"]] = file["name"]
            save_file_map(file_map)
        return

    max_retries = 3
    backoff = 1.0
    for attempt in range(1, max_retries + 1):
        try:
            request = service.files().get_media(fileId=file["id"])
            tmp_path = local_path + ".part"
            with io.FileIO(tmp_path, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        print(f"⬇️ Downloading {file['name']}: {int(status.progress() * 100)}%")
            os.replace(tmp_path, local_path)
            print(f"✅ Download complete: {local_path}")

            if file_map is not None:
                file_map[file["id"]] = file["name"]
                save_file_map(file_map)

            # 🧾 Embed file into RAG *before* announcing Drive check
            print(f"⚙️ Creating embeddings for {file['name']}...")
            try:
                embed_and_store(local_path)
                print(f"✅ Embedding complete for {file['name']}")
                print(f"📦 Now trying to store embeddings in DB for {file['name']}...")
                print(f"🧾 Embeddings stored for {file['name']}")
            except Exception as e:
                print(f"❌ Error during embedding of {file['name']}: {e}")
                # do not delete the downloaded file on embedding error, keep it for retry/inspection

            # Only after embedding is done or failed (but file saved)
            print("🔍 Checking Drive for further changes...")
            return

        except HttpError as e:
            err_text = str(e)
            print(f"HttpError downloading {file.get('name')}: {err_text}")
            if "404" in err_text:
                print(f"🗑️ File not found on Drive during download: {file.get('name')} -> treating as deleted")
                delete_local_file(file["id"], file_map or {})
                return
        except Exception as e:
            print(f"Error downloading {file.get('name')} (attempt {attempt}): {e}")

        if attempt < max_retries:
            time.sleep(backoff)
            backoff *= 2
            print(f"Retrying download (attempt {attempt+1}/{max_retries}) for {file.get('name')}")
        else:
            print(f"Failed to download {file.get('name')} after {max_retries} attempts")
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