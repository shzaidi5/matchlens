"""Abuse limits for the public endpoints that can spend model tokens.

In memory and per replica: enough to stop one visitor from running up the
Foundry bill, without a shared store. Limits come from the environment:

    MATCHLENS_ASK_PER_MIN      questions per visitor per minute    (default 5)
    MATCHLENS_ASK_PER_DAY      questions per replica per day      (default 500)
    MATCHLENS_STREAMS_PER_IP   open streams per visitor            (default 4)
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """The visitor's address. Azure Container Apps' ingress appends the real
    client to X-Forwarded-For, so the last entry is the one it vouches for;
    earlier entries can be set by the client."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class Window:
    """At most `limit` hits per `seconds` for each key (sliding window)."""

    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> float:
        """Record a hit and return 0, or return the seconds until one is allowed."""
        now = time.monotonic() if now is None else now
        hits = self.hits[key]
        while hits and now - hits[0] >= self.seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return self.seconds - (now - hits[0])
        hits.append(now)
        if len(self.hits) > 10_000:  # forget idle visitors
            for k in [k for k, v in self.hits.items() if not v]:
                del self.hits[k]
        return 0.0


class Limits:
    def __init__(self) -> None:
        self.ask_per_ip = Window(int(os.getenv("MATCHLENS_ASK_PER_MIN", "5")), 60)
        self.ask_total = Window(int(os.getenv("MATCHLENS_ASK_PER_DAY", "500")), 86_400)
        self.streams_per_ip = int(os.getenv("MATCHLENS_STREAMS_PER_IP", "4"))
        self.open_streams: dict[str, int] = defaultdict(int)

    def ask(self, request: Request) -> None:
        wait = self.ask_per_ip.check(client_ip(request))
        if wait:
            raise HTTPException(429, f"Too many questions. Try again in {int(wait) + 1}s.",
                                headers={"Retry-After": str(int(wait) + 1)})
        if self.ask_total.check("all"):
            raise HTTPException(429, "The question desk is closed for today. Try again tomorrow.")

    def check_stream(self, request: Request) -> str:
        """Refuse before the response starts; the stream counts itself while it runs."""
        ip = client_ip(request)
        if self.open_streams.get(ip, 0) >= self.streams_per_ip:
            raise HTTPException(429, f"Too many open streams from one address (max {self.streams_per_ip}).")
        return ip

    def open_stream(self, ip: str) -> None:
        self.open_streams[ip] += 1

    def close_stream(self, ip: str) -> None:
        self.open_streams[ip] -= 1
        if self.open_streams[ip] <= 0:
            del self.open_streams[ip]
