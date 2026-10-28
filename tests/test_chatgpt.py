import json

import pytest

from session_vault.chatgpt import ChatGPTClient, VaultError, parse_cookies


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "Cookie: __Secure-next-auth.session-token=fictional; cf_clearance=fake",
            {"__Secure-next-auth.session-token": "fictional", "cf_clearance": "fake"},
        ),
        (
            '{"__Secure-next-auth.session-token.0":"part0","__Secure-next-auth.session-token.1":"part1"}',
            {
                "__Secure-next-auth.session-token.0": "part0",
                "__Secure-next-auth.session-token.1": "part1",
            },
        ),
        (
            json.dumps(
                [
                    {
                        "name": "__Secure-next-auth.session-token",
                        "value": "fake",
                        "domain": ".chatgpt.com",
                    },
                    {"name": "other", "value": "ignored", "domain": "example.com"},
                ]
            ),
            {"__Secure-next-auth.session-token": "fake"},
        ),
    ],
)
def test_cookie_formats(raw, expected):
    assert parse_cookies(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "unrelated=value",
        "Cookie: x=x\nAuthorization: fake",
        '{"__Secure-next-auth.session-token":"fake\\r\\nInjected: yes"}',
        "[1,2]",
        '"invalid"',
    ],
)
def test_invalid_cookie_never_echoed(raw):
    with pytest.raises(VaultError) as error:
        parse_cookies(raw)
    assert "Injected" not in str(error.value)


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


def test_fixed_origin_auth_and_bounded_pagination(monkeypatch):
    calls = []

    class Session:
        def __init__(self, **kwargs):
            assert kwargs == {"impersonate": "chrome", "trust_env": False}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            assert url.startswith("https://chatgpt.com/")
            assert kwargs["allow_redirects"] is False
            if url.endswith("/api/auth/session"):
                return Response({"accessToken": "fictional-token"})
            assert kwargs["headers"]["Authorization"] == "Bearer fictional-token"
            start = kwargs["params"]["offset"]
            size = kwargs["params"]["limit"]
            return Response(
                {
                    "items": [
                        {
                            "id": f"00000000-0000-4000-8000-{i:012d}",
                            "title": "Synthetic",
                            "update_time": 1000 - i,
                        }
                        for i in range(start, start + size)
                    ]
                }
            )

    monkeypatch.setattr("session_vault.chatgpt.requests.Session", Session)
    client = ChatGPTClient({"__Secure-next-auth.session-token": "fictional"})
    client.authenticate()
    assert len(client.list_conversations(30)) == 30
    assert len(calls) == 3
    assert calls[1][1]["params"]["limit"] == 28
    assert calls[2][1]["params"]["limit"] == 2
    assert calls[2][1]["params"]["offset"] == 28


def test_upstream_error_does_not_expose_body(monkeypatch):
    class Session:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, *_args, **_kwargs):
            return Response({"secret": "do-not-leak"}, 403)

    monkeypatch.setattr("session_vault.chatgpt.requests.Session", Session)
    with pytest.raises(VaultError, match="403") as error:
        ChatGPTClient({}).authenticate()
    assert "do-not-leak" not in str(error.value)
