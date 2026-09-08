"""Per-user runtime state. Deliberately in-memory: nothing here is worth a DB."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(slots=True)
class UserState:
    password: str | None = None
    force_document: bool = False


class Store:
    def __init__(self, max_concurrent: int) -> None:
        self._users: dict[int, UserState] = {}
        self._busy: set[int] = set()
        self._slots = asyncio.Semaphore(max_concurrent)

    def user(self, user_id: int) -> UserState:
        return self._users.setdefault(user_id, UserState())

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._busy

    def acquire_user(self, user_id: int) -> bool:
        if user_id in self._busy:
            return False
        self._busy.add(user_id)
        return True

    def release_user(self, user_id: int) -> None:
        self._busy.discard(user_id)

    @property
    def slots(self) -> asyncio.Semaphore:
        return self._slots
