import io
import json
import sys

import pytest
from conftest import CID, FakeChatGPT

from session_vault.__main__ import main


def test_cli_list_stdin(monkeypatch, capsys, conversation_data):
    monkeypatch.setattr(sys, "argv", ["session-vault", "list", "--cookie-stdin", "--json"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("__Secure-next-auth.session-token=fictional"))
    monkeypatch.setattr(
        "session_vault.__main__.ChatGPTClient", lambda *_args: FakeChatGPT(conversation_data)
    )
    monkeypatch.setattr(FakeChatGPT, "authenticate", lambda _self: None, raising=False)
    main()
    result = capsys.readouterr()
    assert len(json.loads(result.out)) == 5
    assert "fictional" not in result.out + result.err


def test_cli_real_export(monkeypatch, capsys, conversation_data, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["session-vault", "export", "--cookie-stdin", "--id", CID, "--output", str(tmp_path)],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("__Secure-next-auth.session-token=fictional"))
    monkeypatch.setattr(
        "session_vault.__main__.ChatGPTClient", lambda *_args: FakeChatGPT(conversation_data)
    )
    monkeypatch.setattr(FakeChatGPT, "authenticate", lambda _self: None, raising=False)
    main()
    assert len(list(tmp_path.glob("*/*.pdf"))) == 1
    assert len(list(tmp_path.glob("*/session-archives.zip"))) == 1
    assert "fictional" not in capsys.readouterr().out


def test_cli_invalid_cookie_has_safe_exit(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["session-vault", "list", "--cookie-stdin"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("invalid-secret-cookie"))
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 1
    assert "invalid-secret-cookie" not in capsys.readouterr().err
