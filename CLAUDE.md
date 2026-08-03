# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

ManyOS is currently in the **design/planning stage** — no application code exists yet.
The repository only contains architecture and planning documents under `docs/`, plus
empty `modules/` and `tests/` directories reserved for future code. There are no
build, lint, or test commands yet because there is nothing to build. Once the first
module (Many Ingest) is implemented, this file should be updated with the actual
commands (e.g. how to install dependencies, run the CLI, run tests).

## What ManyOS is

ManyOS is ManyFast's internal AI operating system for video production. ManyFast is a
video production agency whose core promise is **24H delivery** — turnaround time from
brief to delivered video is the primary business metric ManyOS exists to protect and
improve. ManyOS is not a single tool but an orchestration layer that will eventually
connect production stages (intake → asset ingestion → editing → review → delivery) via
an event-driven core, with AI agents automating specific steps (transcription, tagging,
rough cuts, notifications).

## Terminology

Use ManyFast-specific naming for **user-facing/conceptual** writing (docs, comments that
explain intent, UI text). Generic technical terms are fine for code-level identifiers
(interfaces, classes, file names) — the mapping below governs how concepts are described
to humans, not internal implementation names.

| Generic term | Use instead |
|---|---|
| Manifest schema | ManyFast Asset Schema |
| File organizer | ManyOS Ingest Engine |
| Project folder | ManyOS Project Workspace |
| Metadata | Asset Intelligence Data |

Example: the `Manifest` interface and `manifest.py` module can keep those technical
names in code, but when explaining to a reader what that data represents, call it the
**ManyFast Asset Schema**.

Read these in order before working on anything in this repo:
1. `docs/MANYOS_ARCHITECTURE.md` — full target architecture, all planned modules, and
   the v0.1→v1.0 roadmap.
2. `docs/MANYOS_V0.1_PROPOSAL.md` — ROI-driven reprioritization: explicitly narrows
   scope down to what actually saves time in the first 30 days, and defers most of the
   architecture doc's scope.
3. `docs/MANY_INGEST_BUILD_PLAN.md` — the concrete build plan for the first module
   actually being built: **Many Ingest** (automatically organizing raw video files
   after a shoot).
4. `docs/MANY_INGEST_CLOUD_READY_ARCHITECTURE.md` — binding architectural constraints
   for how Many Ingest must be structured so it can migrate from local-only to a
   server/cloud setup without a rewrite. Read this before writing any Many Ingest code.

## Architectural decisions already locked in for Many Ingest

These are decided, not open questions — follow them rather than re-deriving an
approach when implementing this module:

- **Ports & adapters (hexagonal architecture).** Core ingest logic must depend only on
  abstract interfaces (`Storage`, `Manifest`, `MetadataExtractor`), never directly on
  `os.path`/`shutil` or SQLite-specific calls. Local implementations (filesystem,
  SQLite) are adapters behind those interfaces; cloud implementations (S3, Postgres)
  are meant to be added later as drop-in replacements without touching core logic.
- **Content-addressable asset identity.** Assets are identified by SHA-256 checksum,
  never by file path or an autoincrement ID — this is what makes multi-machine/cloud
  migration safe later.
- **The ManyFast Asset Schema (the manifest's schema) is designed multi-tenant-shaped
  from day one**, even though v0.1 is single-user/local: it includes `client_id`,
  `project_id`, `ingest_run_id`, `operator`, `source_machine` fields, populated with
  local defaults for now.
- **CLI is a thin adapter only.** Argument parsing and adapter wiring (the
  "composition root") live in `cli.py`; no business logic belongs there. This is what
  lets a future HTTP API or worker process call the same `IngestService` unchanged.
- **Configuration is externalized** (YAML file locally) rather than hardcoded, so the
  same structure can later be backed by environment variables/a secrets manager.
- **Structured (JSON-lines) logging of ingest events**, not free-text logs — this is
  meant to seed a future event bus, so log statements should be structured from the
  start rather than retrofitted.
- **No cloud infra, message queue, or multi-user support in v0.1.** Many Ingest v0.1 is
  a manually-triggered local CLI tool; deliberately no background daemon/file-watcher,
  no AI transcription/tagging, no web UI, no automatic deletion of source files (SD
  card clearing is always an explicit, human-confirmed action).
