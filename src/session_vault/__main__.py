import argparse
import getpass
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from uuid import uuid4

from session_vault import __version__
from session_vault.archive import archive_conversations
from session_vault.chatgpt import ChatGPTClient, VaultError, conversation_id, parse_cookies
from session_vault.drive import connect_drive, upload_pdfs


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="session-vault", description="Export ChatGPT conversations to PDF, Markdown and JSON."
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Open the local web UI (loopback only)")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true")
    serve.add_argument("--output", type=Path)
    for name, help_text in (
        ("list", "List recent conversations"),
        ("export", "Export selected or recent conversations"),
    ):
        sub = commands.add_parser(name, help=help_text)
        cookies = sub.add_mutually_exclusive_group()
        cookies.add_argument("--cookie-file", type=Path, help="Read cookies from a local file")
        cookies.add_argument("--cookie-stdin", action="store_true", help="Read cookies from stdin")
        sub.add_argument(
            "--user-agent", default="", help="Match your browser if ChatGPT returns 403"
        )
        sub.add_argument("--account-id", default="", help="Optional ChatGPT workspace/account ID")
        sub.add_argument("--limit", type=int, default=5, help="Most recently updated chats (1-500)")
        sub.add_argument("--archived", action="store_true", help="Read archived chats instead")
        if name == "list":
            sub.add_argument("--json", action="store_true", help="Machine-readable JSON output")
        else:
            sub.add_argument(
                "--id", action="append", default=[], help="Exact conversation UUID; repeatable"
            )
            sub.add_argument("--output", type=Path, default=Path("exports"))
            sub.add_argument(
                "--google-client",
                type=Path,
                help="OAuth Desktop client JSON; authorize and upload PDFs to Google Drive",
            )
    return root


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
    if args.command == "serve":
        import uvicorn

        from session_vault.app import create_app

        if not 1 <= args.port <= 65535:
            parser().error("port must be between 1 and 65535")
        url = f"http://127.0.0.1:{args.port}"
        print(f"Session Vault → {url}")
        print("Credentials stay in memory. Stop with Ctrl+C to end the session.")
        if not args.no_open:
            timer = threading.Timer(1.2, webbrowser.open, args=(url,))
            timer.daemon = True
            timer.start()
        uvicorn.run(create_app(args.output), host="127.0.0.1", port=args.port, access_log=False)
        return
    try:
        if args.cookie_file:
            raw = args.cookie_file.read_text(encoding="utf-8")
        elif args.cookie_stdin:
            raw = sys.stdin.read(100_001)
        elif sys.stdin.isatty():
            raw = getpass.getpass("Paste ChatGPT Cookie (hidden): ")
        else:
            raise VaultError("Use --cookie-stdin or --cookie-file when stdin is not interactive.")
        client = ChatGPTClient(parse_cookies(raw), args.user_agent, args.account_id)
        raw = ""
        client.authenticate()
        if args.command == "list":
            items = client.list_conversations(args.limit, args.archived)
            if args.json:
                print(json.dumps(items, ensure_ascii=False, indent=2))
            else:
                for item in items:
                    print(f"{item['id']}  {item['updated_at'][:10]}  {item['title']}")
            return
        ids = (
            [conversation_id(cid) for cid in args.id]
            if args.id
            else [item["id"] for item in client.list_conversations(args.limit, args.archived)]
        )
        if not ids:
            raise VaultError("No conversations found for this selection.")
        directory = args.output.expanduser().resolve() / uuid4().hex
        manifest = archive_conversations(
            client,
            ids,
            directory,
            lambda done, total, message: print(f"[{done}/{total}] {message}", file=sys.stderr),
        )
        print(f"Saved {len(manifest['conversations'])} conversations to {directory}")
        if args.google_client:
            config = json.loads(args.google_client.read_text())
            credentials = connect_drive(config)
            uploaded = upload_pdfs(
                credentials, [directory / c["pdf"] for c in manifest["conversations"]]
            )
            print(f"Uploaded {len(uploaded)} PDFs to Google Drive / ChatGPT / Session Archives")
        if manifest["failures"]:
            print(
                f"{len(manifest['failures'])} conversation(s) failed; see manifest.json.",
                file=sys.stderr,
            )
            raise SystemExit(2)
    except VaultError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError):
        print(
            "Error: Could not read a local file or parse its JSON. Check the file and permissions.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
