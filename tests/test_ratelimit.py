import pytest
from fastapi import HTTPException
from starlette.requests import Request

from matchlens.ratelimit import Limits, Window, client_ip


def request(xff=None, host="10.0.0.1"):
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234)})


def test_window_allows_the_limit_then_says_how_long_to_wait():
    w = Window(2, 60)
    assert w.check("a", 0) == 0 and w.check("a", 1) == 0
    assert w.check("a", 2) == pytest.approx(58)
    assert w.check("b", 2) == 0  # per key
    assert w.check("a", 60) == 0  # the first hit has aged out


def test_client_ip_trusts_the_entry_the_ingress_appended():
    assert client_ip(request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"  # first entry is client-controlled
    assert client_ip(request()) == "10.0.0.1"


def test_ask_is_limited_per_visitor(monkeypatch):
    monkeypatch.setenv("MATCHLENS_ASK_PER_MIN", "2")
    limits = Limits()
    limits.ask(request("1.1.1.1"))
    limits.ask(request("1.1.1.1"))
    with pytest.raises(HTTPException) as exc:
        limits.ask(request("1.1.1.1"))
    assert exc.value.status_code == 429 and "Retry-After" in exc.value.headers
    limits.ask(request("2.2.2.2"))  # someone else is unaffected


def test_streams_are_limited_while_open(monkeypatch):
    monkeypatch.setenv("MATCHLENS_STREAMS_PER_IP", "1")
    limits = Limits()
    ip = limits.check_stream(request("1.1.1.1"))
    limits.open_stream(ip)
    with pytest.raises(HTTPException):
        limits.check_stream(request("1.1.1.1"))
    limits.close_stream(ip)
    assert limits.check_stream(request("1.1.1.1")) == ip and not limits.open_streams
