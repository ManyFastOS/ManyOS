"""Structured JSON-lines action log.

A chronological, append-only record of what happened — or, in dry-run, would
happen — during one run. Deliberately separate from the ManyFast Asset Schema, which
tracks current state rather than history (see docs/MANY_INGEST_BUILD_PLAN.md,
section 6). Every line carries `dry_run` so a preview can never be mistaken for a
real action after the fact.
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
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **fields: Any) -> None:
        record = {
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "event": event,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
