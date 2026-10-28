"""Real browser UI workflow with a synthetic provider and real Chromium PDF rendering."""

import os
import socket
import threading
import time
from pathlib import Path

import uvicorn
from conftest import FakeChatGPT
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from session_vault.app import create_app


def test_browser_select_export_download_and_mobile(conversation_data, tmp_path):
    app = create_app(tmp_path / "exports")
    app.state.vault["client"] = FakeChatGPT(conversation_data)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except PlaywrightError:
                browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="en-US")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}")
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="Load chats").click()
            page.locator(".conversation-row").first.wait_for()
            assert page.locator(".conversation-row").count() == 5
            screenshot = os.environ.get("VAULT_SCREENSHOT")
            if screenshot:
                Path(screenshot).parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshot, full_page=True)
            page.locator("#select-all").uncheck()
            page.locator(".chat-check").first.check()
            page.get_by_role("button", name="Create archive").click()
            page.get_by_text("Your archive is ready.", exact=True).wait_for(timeout=60000)
            with page.expect_download() as pending:
                page.get_by_role("button", name="Download ZIP").click()
            download = pending.value
            assert download.suggested_filename == "session-archives.zip"
            download.save_as(tmp_path / "download.zip")
            assert (tmp_path / "download.zip").stat().st_size > 1000
            page.get_by_role("button", name="中文", exact=True).click()
            assert page.get_by_role("heading", name="留住每一段好对话。").is_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
