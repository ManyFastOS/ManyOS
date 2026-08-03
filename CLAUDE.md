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

## Vision & Principles

**`docs/VISION.md` is the canonical source for ManyOS's mission and Core Principles —
read it first, before anything else in this repo.** It is the highest source of truth,
together with this file, for design and development decisions. Its principles are not
duplicated here to avoid the two drifting apart; when evaluating a new module or
feature, apply VISION.md's Decision Filter directly (does it solve a real ManyFast
problem, does it save measurable time, can it be reused, is it the simplest solution,
will it still make sense in three years).

Quick-reference tie-ins to decisions already made in this repo:
- *Local First* / *"migration to cloud infrastructure possible without rebuilding"* →
  the Many Ingest ports & adapters design below.
- *Time is the Primary Metric* / Decision Filter Q2 → `docs/MANYOS_V0.1_PROPOSAL.md`,
  the worked example of applying this filter to ManyOS's full module list.
- *"ManyOS is not generic project management software"* (VISION.md, "What ManyOS is
  NOT") → the ROI proposal's explicit call to use Notion/spreadsheets instead of
  building a custom project tracker in v0.1.
- *Modular by Design* → each module (Many Ingest, and future ones) must stay
  independently replaceable — the reason the ports & adapters approach matters even for
  a single-user local tool.
- *AI as an Assistant* — no module should let AI make final creative decisions
  unsupervised; relevant once modules beyond Many Ingest (which has no AI yet) are
  built.

## Terminology

Use ManyFast-specific naming in documentation and code — avoid generic names when a
ManyFast equivalent exists. This governs how concepts are described to a human reader;
code-level identifiers (interfaces, classes, file names such as `Manifest`,
`manifest.py`) may keep generic/technical names.

| Term | Definition |
|---|---|
| **ManyOS** | The operating system as a whole |
| **Many Ingest** | The asset ingestion engine — the module covered by the Many Ingest docs |
| **ManyFast Asset Schema** | The canonical asset description (what earlier docs called "the manifest schema") |
| **Project Workspace** | The per-project destination folder (what earlier docs called "the project folder") |
| **Asset Intelligence** | The AI analysis layer (transcription, tagging, content understanding) |

**Important distinction:** *Asset Intelligence* is the AI analysis layer — it is **not**
a rename for the plain ffprobe metadata (codec, resolution, duration, timestamp) that
Many Ingest v0.1 extracts. That extraction has no AI involved and is explicitly out of
scope for what "Asset Intelligence" means; it stays plain "metadata" until an AI-driven
analysis module actually exists. An earlier terminology pass conflated the two — this
has been corrected in the Many Ingest docs.

Read these in order before working on anything in this repo:
1. `docs/VISION.md` — mission, Core Principles, success metrics, and the Decision
   Filter every feature must pass.
2. `docs/MANYOS_ARCHITECTURE.md` — full target architecture, all planned modules, and
   the v0.1→v1.0 roadmap.
3. `docs/MANYOS_V0.1_PROPOSAL.md` — ROI-driven reprioritization: explicitly narrows
   scope down to what actually saves time in the first 30 days, and defers most of the
   architecture doc's scope.
4. `docs/MANY_INGEST_BUILD_PLAN.md` — the concrete build plan for the first module
   actually being built: **Many Ingest** (automatically organizing raw video files
   after a shoot).
5. `docs/MANY_INGEST_CLOUD_READY_ARCHITECTURE.md` — binding architectural constraints
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
