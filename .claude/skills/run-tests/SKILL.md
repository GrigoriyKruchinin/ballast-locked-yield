---
name: run-tests
description: Run the Ballast pytest suite (unit, property-based, Postgres) and summarize results. Use after any code change.
---

1. Run `make test` (pytest with pytest-asyncio and hypothesis). For Postgres-backed tests set `TEST_DATABASE_URL`.
2. If the change touches infra, also run `make config`.
3. On failure: read the failing test, fix the code (not the test, unless the test is wrong, and say why), re-run.
4. Report: passed/failed/skipped counts and any flaky tests. Long logs go to a file in the scratchpad.
