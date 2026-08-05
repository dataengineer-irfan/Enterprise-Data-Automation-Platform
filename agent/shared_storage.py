"""
agent/shared_storage.py — "Shared storage" side of the condensed-result
handoff pattern (Section 2.2 rule #5): "Subagents report back condensed
structured results + a pointer to full detail on shared storage (not a
full data dump back through the orchestrator's context) — this is how
Anthropic keeps the lead agent's context from blowing out on large jobs."

Phase-4 stand-in: local JSON blobs on disk, one per handoff, addressed by
an opaque pointer string. Swap for S3/Redis/a blob store later — every
caller goes through `write_detail`/`read_detail`, never the filesystem
directly, so that swap touches this one file.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DetailPointer:
    """What actually crosses back to the Manager: a small, fixed-size
    reference, never the payload itself."""
    pointer: str
    size_bytes: int
    record_count: int


class SharedStorage:
    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def write_detail(self, payload: Any, record_count: int = 0) -> DetailPointer:
        pointer = str(uuid.uuid4())
        path = self._dir / f"{pointer}.json"
        text = json.dumps(payload, indent=2, default=str)
        path.write_text(text)
        return DetailPointer(pointer=pointer, size_bytes=len(text.encode("utf-8")), record_count=record_count)

    def read_detail(self, pointer: str) -> Any:
        path = self._dir / f"{pointer}.json"
        if not path.exists():
            raise KeyError(f"No detail found for pointer {pointer!r}")
        return json.loads(path.read_text())


class RedisSharedStorage:
    """Phase 7: Redis-backed shared storage for multi-worker production scale-out."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl_seconds: int = 86400) -> None:
        import redis
        self.client = redis.Redis.from_url(redis_url)
        self.ttl = ttl_seconds

    def write_detail(self, payload: Any, record_count: int = 0) -> DetailPointer:
        pointer = f"detail:{uuid.uuid4()}"
        text = json.dumps(payload, indent=2, default=str)
        self.client.set(pointer, text, ex=self.ttl)
        return DetailPointer(pointer=pointer, size_bytes=len(text.encode("utf-8")), record_count=record_count)

    def read_detail(self, pointer: str) -> Any:
        data = self.client.get(pointer)
        if data is None:
            raise KeyError(f"No detail found in Redis for pointer {pointer!r}")
        return json.loads(data.decode("utf-8"))
