"""Google Drive / Docs side: OAuth, the output folder, and creating the docs.

Uses the narrow `drive.file` scope: the app can only see files and folders it
created itself, never the rest of your Drive.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"


class GoogleSetupError(RuntimeError):
    pass


def get_credentials(client_secrets: Path, token_path: Path, interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            return creds
        except Exception as exc:  # refresh token revoked/expired
            if not interactive:
                raise GoogleSetupError(
                    f"Google token could not be refreshed ({exc}). Run `plaudio auth-google`."
                ) from exc
    if not interactive:
        raise GoogleSetupError("Not signed in to Google. Run `plaudio auth-google` first.")
    if not client_secrets.exists():
        raise GoogleSetupError(
            f"Missing {client_secrets}. Download your OAuth 'Desktop app' client JSON from "
            "Google Cloud Console and save it there (see README)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)
    return creds


def build_drive(creds):
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=creds, cache_discovery=False)


class DocsWriter:
    def __init__(self, drive: Any):
        self.drive = drive

    def ensure_folder(self, folder_id: str | None, name: str) -> str:
        """Return a usable folder id, creating the folder if needed."""
        if folder_id:
            try:
                meta = self.drive.files().get(fileId=folder_id, fields="id,trashed").execute()
                if not meta.get("trashed"):
                    return folder_id
            except Exception:
                pass  # deleted or inaccessible -> make a new one
        created = self.drive.files().create(
            body={"name": name, "mimeType": FOLDER_MIME}, fields="id"
        ).execute()
        return created["id"]

    def find_existing(self, folder_id: str, recording_id: str) -> str | None:
        """Safety net against duplicates if local state was lost."""
        rid = recording_id.replace("'", "\\'")
        q = (f"'{folder_id}' in parents and trashed = false and "
             f"appProperties has {{ key='plaud_id' and value='{rid}' }}")
        res = self.drive.files().list(q=q, fields="files(id)", pageSize=1).execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None

    def create_doc(self, folder_id: str, title: str, html_body: str, recording_id: str) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(html_body.encode("utf-8")),
                                  mimetype="text/html", resumable=False)
        body = {
            "name": title,
            "mimeType": DOC_MIME,  # ask Drive to convert the HTML into a native Google Doc
            "parents": [folder_id],
            "appProperties": {"plaud_id": recording_id, "source": "plaudio"},
        }
        created = self.drive.files().create(body=body, media_body=media, fields="id").execute()
        return created["id"]
