"""Read-only access to the unofficial ChatGPT web endpoints.

There are deliberately no create, archive, delete or other mutation methods.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from curl_cffi import requests

ORIGIN = "https://chatgpt.com"


class VaultError(Exception):
    """A safe, user-facing error that contains no remote response or credentials."""


def parse_cookies(raw: str) -> dict[str, str]:
    raw = raw.strip()
    if not raw or len(raw) > 100_000:
        raise VaultError("Paste the Cookie request header from chatgpt.com (maximum 100 KB).")
    cookies: dict[str, str] = {}
    try:
        if raw.startswith(("[", "{")):
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                data = data["cookies"]
            if isinstance(data, list):
                for item in data:
                    domain = str(item.get("domain", "chatgpt.com")).lstrip(".")
                    if domain == "chatgpt.com" or domain.endswith(".chatgpt.com"):
                        cookies[str(item["name"])] = str(item["value"])
            elif isinstance(data, dict):
                cookies = {str(k): str(v) for k, v in data.items()}
            else:
                raise ValueError
        else:
            if raw.lower().startswith("cookie:"):
                raw = raw[7:].strip()
            if "\n" in raw or "\r" in raw:
                raise ValueError
            for part in raw.split(";"):
                name, sep, value = part.strip().partition("=")
                if sep:
                    cookies[name] = value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise VaultError(
            "Invalid Cookie format. Paste the Cookie header or a cookie JSON export."
        ) from None
    if not cookies or any(
        not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
        or any(c in value for c in "\r\n;\x00")
        for key, value in cookies.items()
    ):
        raise VaultError("Invalid cookie name or value.")
    if not any("session-token" in name for name in cookies):
        raise VaultError(
            "No session cookie found. Copy the complete Cookie header after signing in."
        )
    return cookies


def conversation_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise VaultError("Invalid conversation ID.") from None


def timestamp(value: Any) -> float:
    try:
        if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        return float(value or 0)
    except (ValueError, TypeError, OverflowError):
        return 0


def iso_date(value: Any) -> str:
    try:
        return datetime.fromtimestamp(timestamp(value), UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


class ChatGPTClient:
    def __init__(self, cookies: dict[str, str], user_agent: str = "", account_id: str = ""):
        self.cookies = cookies
        self.user_agent = user_agent.strip()
        self.account_id = account_id.strip()
        if any(c in self.user_agent + self.account_id for c in "\r\n\x00"):
            raise VaultError("Invalid User-Agent or workspace ID.")
        self.token = ""

    def _get(self, path: str, params: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "Referer": ORIGIN + "/"}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.account_id:
            headers["ChatGPT-Account-ID"] = self.account_id
        try:
            # A fresh transport per call avoids sharing a mutable curl handle between workers.
            with requests.Session(impersonate="chrome", trust_env=False) as session:
                response = session.get(
                    ORIGIN + path,
                    params=params,
                    headers=headers,
                    cookies=self.cookies,
                    timeout=45,
                    allow_redirects=False,
                )
        except requests.RequestsError:
            raise VaultError("Could not reach ChatGPT. Check your network and try again.") from None
        status = response.status_code
        if status in (401, 302, 307):
            raise VaultError("Your ChatGPT session has expired. Sign in and paste a fresh Cookie.")
        if status == 403:
            raise VaultError(
                "ChatGPT refused this request (403). Refresh ChatGPT, copy fresh cookies and "
                "the matching User-Agent, and use the same network as your browser."
            )
        if status == 429:
            raise VaultError("ChatGPT rate limit reached. Wait a few minutes before trying again.")
        if status != 200:
            raise VaultError(f"ChatGPT returned HTTP {status}. No conversation was changed.")
        try:
            data = response.json()
        except ValueError:
            raise VaultError(
                "ChatGPT returned a non-JSON page. Refresh your browser session."
            ) from None
        if not isinstance(data, dict):
            raise VaultError("Unexpected ChatGPT response format.")
        return data

    def authenticate(self) -> None:
        data = self._get("/api/auth/session")
        token = data.get("accessToken")
        if not isinstance(token, str) or not token:
            raise VaultError("No access token in session. Sign in and copy a fresh Cookie header.")
        self.token = token

    def list_conversations(self, limit: int = 20, archived: bool = False) -> list[dict]:
        if not 1 <= limit <= 500:
            raise VaultError("Choose between 1 and 500 conversations.")
        found: dict[str, dict] = {}
        offset = 0
        # Bounded pagination; never enumerate the whole account implicitly.
        for _ in range((limit + 27) // 28):
            size = min(28, limit - len(found))
            data = self._get(
                "/backend-api/conversations",
                {
                    "offset": offset,
                    "limit": size,
                    "order": "updated",
                    "is_archived": str(archived).lower(),
                },
            )
            items = data.get("items")
            if not isinstance(items, list):
                raise VaultError(
                    "ChatGPT conversation list format changed; no archive was created."
                )
            before = len(found)
            for item in items:
                cid = conversation_id(item.get("id", ""))
                found[cid] = {
                    "id": cid,
                    "title": str(item.get("title") or "Untitled conversation"),
                    "updated_at": iso_date(item.get("update_time")),
                    "created_at": iso_date(item.get("create_time")),
                    "update_time": timestamp(item.get("update_time")),
                }
            offset += len(items)
            if len(items) < size or len(found) == before or len(found) >= limit:
                break
        return sorted(found.values(), key=lambda item: item["update_time"], reverse=True)[:limit]

    def fetch_conversation(self, cid: str) -> dict:
        data = self._get("/backend-api/conversation/" + conversation_id(cid))
        if not isinstance(data.get("mapping"), dict) or not data["mapping"]:
            raise VaultError(
                "This conversation has no message tree; refusing to create an empty PDF."
            )
        return data
