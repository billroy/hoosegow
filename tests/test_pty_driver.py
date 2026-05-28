import json
import urllib.error
import urllib.request

import pytest

from server.pty_driver import PtyDriver, PtyDriverError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def close(self):
        return None


def test_rpc_posts_token_and_compact_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["headers"] = dict(request.header_items())
        return FakeResponse({"event": "ok"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = PtyDriver(base_url="http://127.0.0.1:5859/", token="secret").open("term-1")

    assert result == {"event": "ok"}
    assert captured["url"] == "http://127.0.0.1:5859/rpc"
    assert captured["timeout"] == 5.0
    assert captured["body"] == {
        "op": "open",
        "token": "secret",
        "id": "term-1",
        "cwd": "/workspace",
        "shell": "/bin/bash",
        "cols": 100,
        "rows": 30,
    }
    assert captured["headers"]["Content-type"] == "application/json"


def test_write_base64_encodes_terminal_input(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse({"event": "ok"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    PtyDriver(base_url="http://controller", token="secret").write("term-1", "hi\n")

    assert captured["body"] == {
        "op": "write",
        "token": "secret",
        "id": "term-1",
        "data": "aGkK",
    }


def test_poll_sends_token_in_query_and_extends_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse({"events": [], "next_seq": 5})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = PtyDriver(base_url="http://controller", token="secret", timeout=2.0).poll(
        "term-1",
        since=4,
        timeout=3.0,
    )

    assert result == {"events": [], "next_seq": 5}
    assert "id=term-1" in captured["url"]
    assert "since=4" in captured["url"]
    assert "timeout=3.0" in captured["url"]
    assert "token=secret" in captured["url"]
    assert captured["timeout"] == 4.0


def test_http_error_uses_controller_error_message(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 5.0
        raise urllib.error.HTTPError(
            url="http://controller/rpc",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=FakeResponse({"error": "bad token"}),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PtyDriverError, match="bad token"):
        PtyDriver(base_url="http://controller", token="secret").status("term-1")


def test_rejects_event_error_and_non_dict_payload(monkeypatch):
    responses = iter([
        FakeResponse({"event": "error", "error": "boom"}),
        FakeResponse(["not", "a", "dict"]),
    ])

    def fake_urlopen(_request, timeout):
        assert timeout == 5.0
        return next(responses)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    driver = PtyDriver(base_url="http://controller", token="secret")

    with pytest.raises(PtyDriverError, match="boom"):
        driver.status("term-1")
    with pytest.raises(PtyDriverError, match="invalid response"):
        driver.status("term-1")
