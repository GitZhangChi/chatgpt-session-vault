"""Loopback-only UI. Credentials live in process memory, never in browser storage."""

import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from platformdirs import user_data_path
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from session_vault.archive import archive_conversations
from session_vault.chatgpt import ChatGPTClient, VaultError, conversation_id, parse_cookies
from session_vault.drive import connect_drive, upload_pdfs

STATIC = Path(__file__).parent / "static"


class Login(BaseModel):
    cookie: str = Field(min_length=1, max_length=100_000, repr=False)
    user_agent: str = Field(default="", max_length=1000)
    account_id: str = Field(default="", max_length=100)
    proxy: str = Field(default="", max_length=2000, repr=False)


class ExportRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


class DriveConfig(BaseModel):
    client_config: dict = Field(repr=False)


def create_app(output: Path | None = None) -> FastAPI:
    output = output or user_data_path("chatgpt-session-vault") / "exports"
    app = FastAPI(title="ChatGPT Session Vault", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    token = secrets.token_urlsafe(32)
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vault")
    state = {"client": None, "drive": None, "drive_busy": False, "drive_error": "", "generation": 0}
    jobs: dict[str, dict] = {}
    app.state.vault = state
    app.state.jobs = jobs

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if (origin and origin != str(request.base_url).rstrip("/")) or (
                request.headers.get("sec-fetch-site") == "cross-site"
            ):
                return JSONResponse(
                    {"error": "Cross-origin requests are disabled."}, status_code=403
                )
            if request.url.path != "/api/bootstrap" and not secrets.compare_digest(
                request.headers.get("x-vault-token", ""), token
            ):
                return JSONResponse({"error": "Reload the local Vault page."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "form-action 'self'; base-uri 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # Pydantic's default error payload may repeat the submitted Cookie.
        return JSONResponse({"error": "Invalid request. Check the input fields."}, status_code=422)

    @app.exception_handler(VaultError)
    async def vault_error(_request, error):
        return JSONResponse({"error": str(error), "code": error.code}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "token": token,
            "connected": state["client"] is not None,
            "drive_connected": state["drive"] is not None,
            "output": str(output),
        }

    @app.post("/api/connect")
    def connect(body: Login):
        generation = state["generation"]
        client = ChatGPTClient(
            parse_cookies(body.cookie), body.user_agent, body.account_id, body.proxy
        )
        client.authenticate()
        with lock:
            if generation != state["generation"]:
                raise VaultError("Connection cancelled. Connect again when you are ready.")
            state["client"] = client
        return {"connected": True}

    @app.post("/api/disconnect")
    def disconnect():
        with lock:
            state["generation"] += 1
            state["client"] = None
            state["drive"] = None
        return {"connected": False}

    @app.get("/api/conversations")
    def conversations(limit: int = 20, archived: bool = False):
        client = state["client"]
        if client is None:
            raise VaultError("Connect ChatGPT first.")
        return {"items": client.list_conversations(limit, archived)}

    def update_job(jid, **updates):
        with lock:
            jobs[jid].update(updates)

    def export_job(jid, client, ids):
        try:
            manifest = archive_conversations(
                client,
                ids,
                output / jid,
                lambda done, total, message: update_job(
                    jid, done=done, total=total, message=message
                ),
            )
            update_job(jid, status="done", manifest=manifest, message="Your archive is ready")
        except VaultError as error:
            update_job(jid, status="failed", error=str(error))
        except Exception:
            update_job(
                jid, status="failed", error="Export failed locally. No conversation was changed."
            )

    @app.post("/api/export")
    def export(body: ExportRequest):
        client = state["client"]
        if client is None:
            raise VaultError("Connect ChatGPT first.")
        ids = list(dict.fromkeys(conversation_id(cid) for cid in body.ids))
        with lock:
            if any(j["status"] == "running" for j in jobs.values()):
                raise VaultError("An export is already running. Wait for it to finish.")
            if len(jobs) >= 100:
                raise VaultError(
                    "Session job limit reached. Restart Vault; existing files are kept."
                )
            jid = uuid4().hex
            jobs[jid] = {
                "id": jid,
                "status": "running",
                "done": 0,
                "total": len(ids),
                "message": "Starting export",
                "upload_status": "idle",
            }
        pool.submit(export_job, jid, client, ids)
        return {"id": jid}

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        with lock:
            if jid not in jobs:
                raise HTTPException(404, "Job not found")
            return dict(jobs[jid])

    @app.get("/api/download/{jid}/{name}")
    def download(jid: str, name: str):
        with lock:
            entry = jobs.get(jid, {})
            manifest = entry.get("manifest", {})
            allowed = set(manifest.get("sha256", {})) | {"manifest.json", "session-archives.zip"}
            if entry.get("status") != "done" or name not in allowed:
                raise HTTPException(404, "File not found")
        return FileResponse(output / jid / name, filename=name)

    @app.post("/api/drive/connect")
    def drive_connect(body: DriveConfig):
        with lock:
            if state["drive_busy"]:
                raise VaultError("Google authorization is already in progress.")
            state.update(drive_busy=True, drive_error="")
            generation = state["generation"]

        def worker():
            try:
                credentials = connect_drive(body.client_config)
                with lock:
                    if generation == state["generation"]:
                        state["drive"] = credentials
            except VaultError as error:
                state["drive_error"] = str(error)
            except Exception:
                state["drive_error"] = "Google authorization failed. Try again."
            finally:
                state["drive_busy"] = False

        pool.submit(worker)
        return {"pending": True}

    @app.get("/api/drive/status")
    def drive_status():
        return {
            "connected": state["drive"] is not None,
            "pending": state["drive_busy"],
            "error": state["drive_error"],
        }

    @app.post("/api/jobs/{jid}/upload")
    def upload(jid: str):
        credentials = state["drive"]
        if credentials is None:
            raise VaultError("Connect Google Drive first.")
        with lock:
            entry = jobs.get(jid)
            if not entry or entry["status"] != "done":
                raise VaultError("Export must finish before uploading.")
            if entry["upload_status"] in ("running", "done"):
                raise VaultError("This archive is already uploading or uploaded.")
            entry["upload_status"] = "running"
            files = [output / jid / item["pdf"] for item in entry["manifest"]["conversations"]]

        def worker():
            try:
                uploaded = upload_pdfs(credentials, files)
                update_job(jid, upload_status="done", uploaded=uploaded)
            except VaultError as error:
                update_job(jid, upload_status="failed", upload_error=str(error))
            except Exception:
                update_job(
                    jid, upload_status="failed", upload_error="Drive upload failed. PDFs are kept."
                )

        pool.submit(worker)
        return {"upload_status": "running"}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
