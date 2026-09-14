"""Structured JSON-lines action log.

A chronological, append-only record of what happened during a real run.
Deliberately separate from the ManyFast Asset Schema, which tracks current
state rather than history (see docs/MANY_INGEST_BUILD_PLAN.md, section 6).

A dry-run never writes here — a preview only reads (the source, and the
destination's existing state for collision/duplicate checks); it must never
need `log_dir` to be reachable, since nothing about the destination has to
be true yet for a preview to be useful. This is a deliberate product
decision for the desktop app's preview flow, a narrower intent than the
original build plan's "elke actie, ook bij dry-run" note — a real
destination-unreachable problem discovered in manual testing: requiring
`log_dir` for a read-only preview meant an unrelated, unreachable
destination (e.g. an external SSD not currently mounted) made even a
perfectly valid preview of a perfectly readable source fail, with a
misleading "source unreadable" message (the exception was an `OSError` from
this class's own directory creation, caught by a broad `except OSError` a
few layers up that assumed every `OSError` there was about the source).
`self.dry_run` (already a field here) is the smallest possible guard for
this — a real run's logging is completely unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class ActionLogger:
    path: Path
    run_id: str
    dry_run: bool

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        if self.dry_run:
            return  # een preview schrijft nooit naar log_dir — zie moduledocstring
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
