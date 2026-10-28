"""Shared export pipeline used by the CLI and local web application."""

import hashlib
import json
import zipfile
from collections.abc import Callable
from pathlib import Path

from session_vault.chatgpt import ChatGPTClient, VaultError
from session_vault.conversation import parse_conversation
from session_vault.render import write_archive


def archive_conversations(
    client: ChatGPTClient,
    ids: list[str],
    directory: Path,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    results = []
    failures = []
    for index, cid in enumerate(dict.fromkeys(ids)):
        if progress:
            progress(index, len(ids), "Exporting conversation")
        try:
            raw = client.fetch_conversation(cid)
            conversation = parse_conversation(raw, cid)
            results.append(write_archive(conversation, raw, directory))
        except VaultError as error:
            failures.append({"id": cid, "error": str(error)})
    if not results:
        raise VaultError(failures[0]["error"] if failures else "No conversations selected.")
    checksums = {}
    for result in results:
        for kind in ("pdf", "markdown", "json"):
            path = directory / result[kind]
            checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "format_version": 1,
        "branch": "current_node",
        "timezone": "UTC",
        "conversations": results,
        "failures": failures,
        "sha256": checksums,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with zipfile.ZipFile(directory / "session-archives.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in [*checksums, "manifest.json"]:
            archive.write(directory / name, name)
    if progress:
        progress(len(ids), len(ids), "Export finished")
    return manifest
