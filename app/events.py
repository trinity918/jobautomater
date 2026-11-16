"""In-process pub/sub that feeds the dashboard's /ws/events socket, plus a log ring buffer."""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Any

log = logging.getLogger("jobautomater")

_subscribers: set[asyncio.Queue] = set()
_recent: collections.deque = collections.deque(maxlen=300)


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(kind: str, **data: Any) -> None:
    msg = {"kind": kind, "at": time.time(), **data}
    if kind == "log":
        _recent.append(msg)
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def logline(message: str, level: str = "info", **data: Any) -> None:
    getattr(log, level if level in ("info", "warning", "error", "debug") else "info")(message)
    publish("log", level=level, message=message, **data)


def recent_logs() -> list[dict]:
    return list(_recent)
