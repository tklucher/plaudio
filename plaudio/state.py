"""Remembers which recordings have already been turned into Google Docs."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"folder_id": None, "last_run": None, "processed": {}}
        if path.exists():
            self.data.update(json.loads(path.read_text()))

    # --- accessors -------------------------------------------------------
    @property
    def folder_id(self) -> str | None:
        return self.data.get("folder_id")

    @folder_id.setter
    def folder_id(self, value: str) -> None:
        self.data["folder_id"] = value

    @property
    def last_run(self) -> datetime | None:
        raw = self.data.get("last_run")
        return datetime.fromisoformat(raw) if raw else None

    def is_processed(self, recording_id: str) -> bool:
        return recording_id in self.data["processed"]

    def mark_processed(self, recording_id: str, doc_id: str | None, name: str) -> None:
        self.data["processed"][recording_id] = {
            "doc_id": doc_id,
            "name": name,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.save()  # save after every doc so a crash never causes duplicates

    def touch_last_run(self, when: datetime) -> None:
        self.data["last_run"] = when.isoformat(timespec="seconds")
        self.save()

    # --- persistence -----------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)  # atomic
