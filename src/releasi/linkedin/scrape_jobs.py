"""Durable event checkpoints and conservative, account-wide search pacing."""
from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
from pathlib import Path
from uuid import UUID


class ScrapeJobStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.active = {}  # list_id -> account_id, including queued tasks

    def _path(self, key: str) -> Path:
        UUID(key.removeprefix("rate-"))
        return self.directory / (key + ".json")

    def _read(self, key: str) -> dict:
        try:
            return json.loads(self._path(key).read_text())
        except FileNotFoundError:
            return {}

    def _write(self, key: str, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.directory)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(state, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self._path(key))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def read(self, list_id: str) -> dict:
        state = self._read(list_id)
        if state.get("status") == "running" and list_id not in self.active:
            state.update(status="error", error="Run interrupted. Existing attendees are saved; use Re-scrape to resume.")
        return state

    def update(self, list_id: str, **fields) -> dict:
        state = self._read(list_id)
        state.update(fields, updated_at=time.time())
        self._write(list_id, state)
        return state

    def prepare(self, list_id: str, account_id: str, limit=None) -> None:
        self.update(list_id, status="awaiting_login", account_id=account_id,
                    limit=limit, next_page=1, collected=0,
                    error="Reconnect this account, then select Re-scrape.")

    def claim(self, list_id: str, account_id: str, limit=None) -> None:
        if list_id in self.active or account_id in self.active.values():
            raise ValueError("A scrape is already queued or running for this list/account")
        state = self.read(list_id)
        if state.get("account_id") not in (None, account_id) and state.get("next_page", 1) > 1:
            raise ValueError("Resume must use the original account because search ordering may differ")
        self.active[list_id] = account_id
        try:
            self.update(list_id, status="running", account_id=account_id,
                        limit=limit if limit is not None else state.get("limit"),
                        next_page=1 if state.get("status") == "done" else state.get("next_page", 1),
                        collected=state.get("collected", 0), error=None)
        except BaseException:
            self.release(list_id)
            raise

    def release(self, list_id: str) -> None:
        self.active.pop(list_id, None)

    def delete(self, list_id: str) -> None:
        if list_id in self.active:
            raise ValueError("Cannot delete a list while its scrape is running")
        self._path(list_id).unlink(missing_ok=True)

    async def before_navigation(self, account_id: str) -> None:
        key = "rate-" + account_id
        state = self._read(key)
        delay = max(0.0, state.get("next_navigation_at", 0) - time.time())
        if delay:
            await asyncio.sleep(delay)
        now = time.time()
        self._write(key, {"last_navigation_at": now,
                          "next_navigation_at": now + random.uniform(360, 420)})

    def record_page_size(self, account_id: str, size: int) -> None:
        key = "rate-" + account_id
        state = self._read(key)
        state["next_navigation_at"] = max(
            state.get("next_navigation_at", 0),
            state.get("last_navigation_at", time.time()) + max(360, size * 36),
        )
        self._write(key, state)


_store = None


def get_scrape_job_store() -> ScrapeJobStore:
    global _store
    if _store is None:
        from releasi.config import get_settings
        db_path = Path(get_settings().db_url.split("///", 1)[-1])
        _store = ScrapeJobStore(db_path.parent / "event_scrape_jobs")
    return _store
