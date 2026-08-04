"""
core/actor_context.py — per-request "who's actually doing this" context.

Every audit record (`core/audit.py`) needs a real `actor` string, but
`ManagerAgent` is one long-lived singleton shared across every HTTP
request (`backend/dependencies.py`). Before this module existed, the
actor was fixed at construction time (`actor="api"` for every request,
regardless of who was authenticated) — cosmetically fine for a
single-user dev session, actually wrong for a governed audit trail once
more than one person can log in.

`contextvars.ContextVar` (not a plain mutable attribute on ManagerAgent)
is the correct fix, not a documented shortcut: it's per-async-task, so
concurrent FastAPI requests each see their own actor even though they're
all calling into the same `ManagerAgent` instance. A shared mutable
`self._actor` attribute would have a real race under concurrent requests
(request A sets it, request B's audit records land under A's identity
before A finishes) — contextvars don't have that problem by construction.
"""
from __future__ import annotations

import contextvars
from typing import Optional

_current_actor: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_actor", default=None)


def set_current_actor(name: str):
    """Returns a reset token — always release it in a `finally` block
    (see `backend/auth_deps.py`'s `with_actor` dependency) so one request's
    actor never leaks into the next task that happens to reuse this
    context (contextvars propagate to child tasks by default)."""
    return _current_actor.set(name)


def reset_current_actor(token) -> None:
    _current_actor.reset(token)


def get_effective_actor(default: str) -> str:
    """What every audit-writing call site should use instead of a fixed
    constructor-time actor string: the real authenticated user for the
    current request if one is set, otherwise `default` (e.g. for CLI/REPL
    usage — `cli.py`/`chat.py` — which never runs inside a request context)."""
    return _current_actor.get() or default
