"""Process-local, bounded response cache; never used for authorization or playback."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class ResultCache:
    def __init__(self, capacity: int = 128, max_bytes: int = 4 * 1024 * 1024) -> None:
        self.capacity = capacity
        self.max_bytes = max_bytes
        self.entries: OrderedDict[Hashable, tuple[float, int, Any]] = OrderedDict()
        self.bytes = 0
        self.pending: dict[Hashable, asyncio.Task[Any]] = {}
        self.waiters: dict[Hashable, int] = {}

    async def get(
        self, key: Hashable, load: Callable[[], Awaitable[Any]], ttl: float, valid: Callable[[], bool] = lambda: True
    ) -> Any:
        now = time.monotonic()
        for expired in [k for k, (until, _, _) in self.entries.items() if until <= now]:
            self.bytes -= self.entries.pop(expired)[1]
        if key in self.entries:
            self.entries.move_to_end(key)
            return copy.deepcopy(self.entries[key][2])
        if key not in self.pending:
            # Bound coalescing bookkeeping too. Overflow requests remain uncached.
            if len(self.pending) >= self.capacity:
                return await load()

            async def run() -> Any:
                value = await load()
                size = len(json.dumps(value, default=str).encode())
                if valid() and size <= min(self.max_bytes, 512 * 1024):
                    while self.entries and (len(self.entries) >= self.capacity or self.bytes + size > self.max_bytes):
                        self.bytes -= self.entries.popitem(last=False)[1][1]
                    self.entries[key] = (time.monotonic() + ttl, size, copy.deepcopy(value))
                    self.bytes += size
                return value

            self.pending[key] = asyncio.create_task(run())
            self.waiters[key] = 0
        task = self.pending[key]
        self.waiters[key] += 1
        try:
            return copy.deepcopy(await asyncio.shield(task))
        finally:
            self.waiters[key] -= 1
            if not self.waiters[key]:
                self.pending.pop(key, None)
                self.waiters.pop(key, None)
                if not task.done():
                    task.cancel()


class CatalogRevisions:
    """Commit-scoped revisions. Profile updates never evict unrelated profiles."""

    GLOBAL = {
        "source", "title", "channel", "channel_group", "programme", "setting", "service_registry", "age_classification"
    }
    PERSONAL = {"profile", "favorite", "progress", "watchlist"}

    def __init__(self) -> None:
        self.catalog = 0
        self.personal_fallback = 0
        self.profiles: dict[str, int] = {}

    def version(self, profile: str) -> tuple[int, int, int]:
        return self.catalog, self.personal_fallback, self.profiles.get(profile, 0)

    def observe(self, session: Any) -> None:
        from sqlalchemy import event
        from sqlalchemy.sql import operators, visitors
        from sqlalchemy.sql.elements import BinaryExpression, BindParameter

        changed_global = False
        changed_profiles: set[str] = set()
        unknown_personal = False

        def mark(table: str, profiles: set[str]) -> None:
            nonlocal changed_global, unknown_personal
            if table in self.GLOBAL:
                changed_global = True
            if table in self.PERSONAL:
                if profiles:
                    changed_profiles.update(profiles)
                else:
                    unknown_personal = True

        @event.listens_for(session, "before_flush")
        def flush(s: Any, *_: Any) -> None:
            for obj in s.new | s.dirty | s.deleted:
                table = obj.__table__.name
                # Navigation snapshots are derived read state, not catalog preferences.
                if table == "setting" and str(getattr(obj, "key", "")).startswith("chlist:"):
                    continue
                ident = getattr(obj, "id" if table == "profile" else "profile_id", None)
                mark(table, {str(ident)} if ident is not None else set())

        @event.listens_for(session, "do_orm_execute")
        def execute(state: Any) -> None:
            if not (state.is_insert or state.is_update or state.is_delete):
                return
            statement = state.statement
            table = statement.table.name
            if table not in self.PERSONAL:
                mark(table, set())
                return
            column = "id" if table == "profile" else "profile_id"
            profiles: set[str] = set()
            # Do not compile ORM bulk INSERTs before SQLAlchemy's execution phase.
            # Their internal mapper annotations are populated only during execution.
            for field, value in (getattr(statement, "_values", None) or {}).items():
                if (
                    getattr(field, "name", field) == column
                    and isinstance(value, BindParameter)
                    and value.value is not None
                ):
                    profiles.add(str(value.value))
            supplied = state.parameters or []
            for row in supplied if isinstance(supplied, list) else [supplied]:
                if row.get(column) is not None:
                    profiles.add(str(row[column]))
            for criterion in getattr(statement, "_where_criteria", ()):
                for node in visitors.iterate(criterion):
                    if (
                        isinstance(node, BinaryExpression)
                        and node.operator is operators.eq
                        and getattr(node.left, "name", None) == column
                        and isinstance(node.right, BindParameter)
                        and node.right.value is not None
                    ):
                        profiles.add(str(node.right.value))
            mark(table, profiles)

        def clear() -> None:
            nonlocal changed_global, unknown_personal
            changed_global = unknown_personal = False
            changed_profiles.clear()

        @event.listens_for(session, "after_commit")
        def commit(_: Any) -> None:
            self.catalog += int(changed_global)
            self.personal_fallback += int(unknown_personal)
            for profile in changed_profiles:
                self.profiles[profile] = self.profiles.get(profile, 0) + 1
            clear()

        @event.listens_for(session, "after_rollback")
        def rollback(_: Any) -> None:
            clear()
