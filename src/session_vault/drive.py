"""Optional Google Drive integration with per-run OAuth credentials held in memory."""

from pathlib import Path

from google.auth.exceptions import GoogleAuthError
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException

from session_vault.chatgpt import VaultError

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def connect_drive(config: dict):
    if not isinstance(config, dict):
        raise VaultError("Choose a Google OAuth client JSON object for a Desktop app.")
    installed = config.get("installed")
    if not isinstance(installed, dict):
        raise VaultError("Choose a Google OAuth client JSON for a Desktop app.")
    client_id = installed.get("client_id", "")
    if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com"):
        raise VaultError("Invalid Google OAuth client ID.")
    # Never trust endpoint URLs from uploaded JSON with credentials or network requests.
    safe_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": installed.get("client_secret", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    try:
        flow = InstalledAppFlow.from_client_config(safe_config, SCOPES)
        return flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            timeout_seconds=180,
            authorization_prompt_message="Complete Google Drive authorization in your browser.",
            success_message="Google Drive connected. You can close this tab.",
        )
    except (GoogleAuthError, OAuth2Error, RequestException, ValueError, OSError, AttributeError):
        raise VaultError(
            "Google authorization failed or timed out. Check your OAuth Desktop client."
        ) from None


def _folder(service, name: str, parent: str) -> str:
    query = (
        f"name = '{name}' and '{parent}' in parents and trashed = false "
        "and mimeType = 'application/vnd.google-apps.folder'"
    )
    items = service.files().list(q=query, fields="files(id)", pageSize=10).execute()["files"]
    if items:
        return items[0]["id"]
    return (
        service.files()
        .create(
            body={
                "name": name,
                "parents": [parent],
                "mimeType": "application/vnd.google-apps.folder",
            },
            fields="id",
        )
        .execute()["id"]
    )


def upload_pdfs(credentials, files: list[Path]) -> list[dict]:
    try:
        with build("drive", "v3", credentials=credentials, cache_discovery=False) as service:
            root = _folder(service, "ChatGPT", "root")
            parent = _folder(service, "Session Archives", root)
            uploaded = []
            for path in files:
                media = MediaFileUpload(str(path), mimetype="application/pdf", resumable=True)
                request = service.files().create(
                    body={"name": path.name, "parents": [parent]},
                    media_body=media,
                    fields="id,name,webViewLink",
                )
                result = None
                while result is None:
                    _, result = request.next_chunk(num_retries=2)
                uploaded.append(result)
            return uploaded
    except (HttpError, GoogleAuthError, OSError):
        raise VaultError(
            "Google Drive upload failed. Local PDFs are safe. Some files may already be uploaded; "
            "check Drive before retrying to avoid duplicates."
        ) from None
