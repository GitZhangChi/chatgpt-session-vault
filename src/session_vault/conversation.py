"""Turn a ChatGPT message tree into the currently selected conversation branch."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from session_vault.chatgpt import VaultError, timestamp


@dataclass
class Message:
    role: str
    text: str
    created_at: float


@dataclass
class Conversation:
    id: str
    title: str
    updated_at: float
    messages: list[Message]
    warnings: list[str]

    @property
    def filename(self) -> str:
        title = unicodedata.normalize("NFKC", self.title)
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", title)
        title = re.sub(r"\s+", " ", title).strip(" ._")[:80] or "Untitled"
        # Filesystems limit bytes, not characters; long CJK/emoji titles must also fit.
        title = title.encode("utf-8")[:180].decode("utf-8", errors="ignore")
        try:
            date = datetime.fromtimestamp(self.updated_at, UTC).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            date = "undated"
        return f"{date}_{title}_{self.id[:8]}"

    def markdown(self) -> str:
        chunks = [f"# {self.title}", f"Source: https://chatgpt.com/c/{self.id}"]
        if self.warnings:
            chunks.append("## Export notes\n\n" + "\n".join("- " + w for w in self.warnings))
        chunks.extend(f"## {m.role.title()}\n\n{m.text}" for m in self.messages)
        return "\n\n".join(chunks) + "\n"


def parse_conversation(data: dict, cid: str) -> Conversation:
    mapping = data.get("mapping", {})
    if not isinstance(mapping, dict) or not mapping:
        raise VaultError("No message tree found in this conversation.")
    warnings: list[str] = []
    node_id = data.get("current_node")
    if not node_id or node_id not in mapping:
        # A branch cannot safely be guessed from node dates or dictionary insertion order.
        raise VaultError("Conversation has no valid current_node; cannot select a branch reliably.")
    chain = []
    visited = set()
    while node_id:
        if node_id in visited or node_id not in mapping:
            raise VaultError("Conversation tree is cyclic or incomplete; export stopped.")
        visited.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            raise VaultError("Conversation contains an invalid message node.")
        chain.append(node)
        node_id = node.get("parent")
    messages = []
    for node in reversed(chain):
        message = node.get("message")
        if not message:
            continue
        role = message.get("author", {}).get("role")
        metadata = message.get("metadata") or {}
        if role not in ("user", "assistant") or metadata.get(
            "is_visually_hidden_from_conversation"
        ):
            continue
        # Internal analysis/tool-directed messages are not user-facing answers.
        if message.get("channel") in ("analysis", "justify", "confidence"):
            continue
        if message.get("recipient", "all") not in (None, "all"):
            continue
        content = message.get("content") or {}
        parts = content.get("parts", [])
        texts = []
        for part in parts:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                else:
                    kind = part.get("content_type", "attachment")
                    texts.append(f"[Non-text content: {kind}; original asset not downloaded]")
                    warnings.append(
                        "Some images/audio/attachments are represented by placeholders."
                    )
        if not parts and isinstance(content.get("text"), str):
            texts.append(content["text"])
        if metadata.get("attachments"):
            warnings.append("Uploaded attachment binaries are not included in this archive.")
        if not texts and content.get("content_type") not in (None, "text"):
            texts.append(f"[Non-text content: {content['content_type']}; not downloaded]")
            warnings.append("Some non-text content could not be exported.")
        text = "\n\n".join(texts).strip()
        if text:
            messages.append(Message(role, text, timestamp(message.get("create_time"))))
        if message.get("status") not in (None, "finished_successfully"):
            warnings.append("At least one message was unfinished or interrupted at export time.")
    if not messages:
        raise VaultError("No visible user or assistant messages found; no empty PDF was generated.")
    if any(len(n.get("children") or []) > 1 for n in chain):
        warnings.append(
            "Only the currently selected branch is rendered; other branches remain in JSON."
        )
    return Conversation(
        cid,
        str(data.get("title") or "Untitled conversation"),
        timestamp(data.get("update_time") or data.get("create_time")),
        messages,
        list(dict.fromkeys(warnings)),
    )
