# Many Ingest

ManyOS's asset ingestion engine — automatically organizes raw video files after a
shoot. Local-only CLI tool, v0.1.

Design and scope: see `../../docs/MANY_INGEST_BUILD_PLAN.md` and
`../../docs/MANY_INGEST_CLOUD_READY_ARCHITECTURE.md`. Terminology and locked-in
architectural decisions: see `../../CLAUDE.md`.

Status: under construction, built step by step per the approved implementation plan.

## Testing

```
.venv/bin/pytest                    # normal suite — excludes crash-isolation tests
.venv/bin/pytest -m crash_isolation # opt-in: intentionally crashes a child QProcess
                                     # with SIGSEGV per test, to prove the GUI survives
                                     # it. Real crashes, so macOS may show a crash
                                     # report for the (expected) child crash — run
                                     # this separately, not as part of routine testing.
```
