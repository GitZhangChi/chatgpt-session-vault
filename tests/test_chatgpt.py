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
    def __init__(self, data, status=200, headers=None, text=""):
        self.data, self.status_code = data, status
        self.headers = headers or {}
        self.text = text

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
    assert calls[0][1]["proxy"] is None


def test_explicit_proxy_is_passed_without_changing_origin(monkeypatch):
    calls = []

    class Session:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response({"accessToken": "fictional-token"})

    monkeypatch.setattr("session_vault.chatgpt.requests.Session", Session)
    ChatGPTClient({}, proxy="http://127.0.0.1:8080").authenticate()
    assert calls[0][0] == "https://chatgpt.com/api/auth/session"
    assert calls[0][1]["proxy"] == "http://127.0.0.1:8080"


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


@pytest.mark.parametrize(
    "response,code",
    [
        (Response({}, 403, {"cf-mitigated": "challenge"}, "private-body"), "cloudflare_challenge"),
        (
            Response(
                {}, 403, {"content-type": "text/html"}, "<title>Just a moment</title>private-body"
            ),
            "cloudflare_challenge",
        ),
        (Response({"error": "private-body"}, 403), "access_denied"),
        (Response({}, 401), "session_expired"),
    ],
)
def test_access_failure_classification_never_retries_or_exposes_response(
    monkeypatch, response, code
):
    calls = []

    class Session:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, *_args, **_kwargs):
            calls.append(1)
            return response

    monkeypatch.setattr("session_vault.chatgpt.requests.Session", Session)
    with pytest.raises(VaultError) as error:
        ChatGPTClient({}).authenticate()
    assert error.value.code == code
    assert "private-body" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "proxy",
    [
        "http://user:private@localhost:8080",
        "http://localhost:bad",
        "file:///tmp/proxy",
        "http://localhost:8080/private",
    ],
)
def test_invalid_proxy_does_not_expose_input(proxy):
    with pytest.raises(VaultError) as error:
        ChatGPTClient({}, proxy=proxy)
    assert "private" not in str(error.value)
