#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EventLogger:
    def __init__(self, protocol: str, log_file: str | None = None):
        self.protocol = protocol
        self.log_file = log_file
        self._handle = None
        if log_file:
            path = Path(log_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = path.open("a", encoding="utf-8")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def log(self, *, timestamp: float, kind: str, data: dict[str, Any]) -> None:
        if self._handle is None:
            return
        record = {
            "timestamp": timestamp,
            "protocol": self.protocol,
            "kind": kind,
            "data": data,
        }
        self._handle.write(json.dumps(record, sort_keys=True) + "\n")
        self._handle.flush()
