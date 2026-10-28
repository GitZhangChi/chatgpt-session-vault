import threading
import time

from conftest import CID, FakeChatGPT
from fastapi.testclient import TestClient

from session_vault.app import create_app


def make_client(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app, base_url="http://127.0.0.1")
    token = client.get("/api/bootstrap").json()["token"]
    client.headers["x-vault-token"] = token
    return app, client


def test_origin_host_and_token_checks(tmp_path):
    _, client = make_client(tmp_path)
    assert client.get("/api/conversations", headers={"x-vault-token": "wrong"}).status_code == 403
    assert (
        client.get("/api/bootstrap", headers={"origin": "https://evil.invalid"}).status_code == 403
    )
    assert client.get("/api/bootstrap", headers={"host": "evil.invalid"}).status_code == 400
    assert (
        client.post("/api/disconnect", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    )
    assert client.get("/").headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_validation_errors_never_echo_secrets(tmp_path):
    _, client = make_client(tmp_path)
    secret = "fictional-secret-never-repeat"
    response = client.post("/api/connect", json={"cookie": {"secret": secret}})
    assert response.status_code == 422
    assert secret not in response.text


def test_disconnect_cannot_be_undone_by_pending_oauth(monkeypatch, tmp_path):
    app, client = make_client(tmp_path)
    started, finish = threading.Event(), threading.Event()

    def authorize(_config):
        started.set()
        finish.wait(timeout=5)
        return object()

    monkeypatch.setattr("session_vault.app.connect_drive", authorize)
    assert client.post("/api/drive/connect", json={"client_config": {}}).status_code == 200
    assert started.wait(timeout=5)
    client.post("/api/disconnect")
    finish.set()
    deadline = time.monotonic() + 5
    while app.state.vault["drive_busy"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.state.vault["drive"] is None


def test_connect_forget_and_read_list(monkeypatch, tmp_path, conversation_data):
    app, client = make_client(tmp_path)
    assert client.get("/api/conversations").status_code == 400
    monkeypatch.setattr(
        "session_vault.app.ChatGPTClient", lambda *_args: FakeChatGPT(conversation_data)
    )
    monkeypatch.setattr(FakeChatGPT, "authenticate", lambda _self: None, raising=False)
    response = client.post(
        "/api/connect", json={"cookie": "__Secure-next-auth.session-token=fictional"}
    )
    assert response.status_code == 200
    assert "fictional" not in response.text
    assert len(client.get("/api/conversations?limit=5").json()["items"]) == 5
    client.post("/api/disconnect")
    assert app.state.vault["client"] is None


def test_real_export_job_download_and_drive_upload(monkeypatch, tmp_path, conversation_data):
    app, client = make_client(tmp_path)
    app.state.vault["client"] = FakeChatGPT(conversation_data)
    jid = client.post("/api/export", json={"ids": [CID]}).json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{jid}").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    name = job["manifest"]["conversations"][0]["pdf"]
    response = client.get(f"/api/download/{jid}/{name}")
    assert response.content.startswith(b"%PDF-")
    assert client.get(f"/api/download/{jid}/not-allowed.txt").status_code == 404
    assert client.post(f"/api/jobs/{jid}/upload").status_code == 400
    app.state.vault["drive"] = object()
    calls = []

    def upload(_credentials, files):
        calls.extend(files)
        return [{"id": "fictional-google-file-id", "name": files[0].name}]

    monkeypatch.setattr("session_vault.app.upload_pdfs", upload)
    assert client.post(f"/api/jobs/{jid}/upload").status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{jid}").json()
        if job["upload_status"] != "running":
            break
        time.sleep(0.05)
    assert job["upload_status"] == "done"
    assert calls == [tmp_path / jid / name]
    assert client.post(f"/api/jobs/{jid}/upload").status_code == 400
