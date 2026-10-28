from unittest.mock import MagicMock

import pytest
from oauthlib.oauth2 import OAuth2Error

from session_vault.chatgpt import VaultError
from session_vault.drive import SCOPES, connect_drive, upload_pdfs


def test_oauth_uses_fixed_google_urls_and_memory_only(monkeypatch):
    flow = MagicMock()
    factory = MagicMock(return_value=flow)
    monkeypatch.setattr("session_vault.drive.InstalledAppFlow.from_client_config", factory)
    connect_drive(
        {
            "installed": {
                "client_id": "fake.apps.googleusercontent.com",
                "client_secret": "synthetic",
                "token_uri": "https://evil.invalid",
            }
        }
    )
    config, scopes = factory.call_args.args
    assert config["installed"]["token_uri"] == "https://oauth2.googleapis.com/token"
    assert scopes == SCOPES == ["https://www.googleapis.com/auth/drive.file"]
    assert flow.run_local_server.call_args.kwargs["host"] == "127.0.0.1"
    assert flow.run_local_server.call_args.kwargs["port"] == 0


def test_drive_creates_scoped_folders_and_resumable_pdf_upload(monkeypatch, tmp_path):
    service = MagicMock()
    service.__enter__.return_value = service
    files = service.files.return_value
    files.list.return_value.execute.side_effect = [{"files": []}, {"files": []}]
    first, second, third = MagicMock(), MagicMock(), MagicMock()
    first.execute.return_value = {"id": "folder-chatgpt"}
    second.execute.return_value = {"id": "folder-archives"}
    third.next_chunk.return_value = (None, {"id": "pdf-file", "name": "example.pdf"})
    files.create.side_effect = [first, second, third]
    monkeypatch.setattr("session_vault.drive.build", MagicMock(return_value=service))
    media = MagicMock()
    monkeypatch.setattr("session_vault.drive.MediaFileUpload", media)
    path = tmp_path / "example.pdf"
    path.write_bytes(b"%PDF-synthetic")
    assert upload_pdfs(object(), [path])[0]["id"] == "pdf-file"
    assert files.create.call_args.kwargs["body"]["parents"] == ["folder-archives"]
    assert media.call_args.kwargs == {"mimetype": "application/pdf", "resumable": True}


def test_invalid_oauth_config():
    with pytest.raises(VaultError):
        connect_drive({"web": {"client_id": "fake"}})


def test_oauth_error_does_not_expose_remote_response(monkeypatch):
    factory = MagicMock(side_effect=OAuth2Error(description="fictional-sensitive-response"))
    monkeypatch.setattr("session_vault.drive.InstalledAppFlow.from_client_config", factory)
    with pytest.raises(VaultError) as error:
        connect_drive({"installed": {"client_id": "fake.apps.googleusercontent.com"}})
    assert "fictional-sensitive-response" not in str(error.value)
