from google.oauth2 import service_account
from googleapiclient.discovery import build

# ------------------- CONFIG -------------------
CREDENTIALS_FILE = "credentials.json"   # your service account JSON
SCOPES = [
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# ------------------- AUTH -------------------
credentials = service_account.Credentials.from_service_account_file(
    CREDENTIALS_FILE, scopes=SCOPES
)

print("✅ Service account email:", credentials.service_account_email)
print("✅ Project ID (from credentials):", credentials.project_id)

drive_service = build("drive", "v3", credentials=credentials)

# ------------------- FIND FOLDER BY NAME -------------------
def get_folder_id_by_name(service, folder_name):
    """Find folder ID by name that is shared with the service account."""
    results = service.files().list(
        q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)",
        pageSize=10
    ).execute()

    folders = results.get("files", [])
    if not folders:
        print(f"❌ Folder '{folder_name}' not found or not shared with this service account.")
        return None
    else:
        folder = folders[0]
        print(f"📂 Found folder: {folder['name']} (id={folder['id']})")
        return folder["id"]

# ------------------- LIST FILES IN FOLDER -------------------
def list_files_in_folder(service, folder_id):
    """List all files in a given folder."""
    results = service.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name, mimeType)"
    ).execute()

    files = results.get("files", [])
    if not files:
        print("📂 No files found in this folder.")
    else:
        print("📄 Files in folder:")
        for f in files:
            print(f"   - {f['name']} ({f['mimeType']}, id={f['id']})")

# ------------------- MAIN -------------------
if __name__ == "__main__":
    folder_name = "project"  # your folder name
    folder_id = get_folder_id_by_name(drive_service, folder_name)
    if folder_id:
        list_files_in_folder(drive_service, folder_id)
