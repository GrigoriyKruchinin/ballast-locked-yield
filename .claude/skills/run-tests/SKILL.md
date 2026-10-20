---
name: run-tests
description: Run the Ballast pytest suite (unit, property-based, integration) and summarize results. Use after any code change.
---

1. Run `make test` (pytest with pytest-asyncio and hypothesis).
2. If the change touches infra, also run `docker compose -f docker-compose.local.yml config` and check service healthchecks.
3. On failure: read the failing test, fix the code (not the test, unless the test is wrong, and say why), re-run.
4. Report: passed/failed/skipped counts, coverage of money/NLY modules if available, and any flaky tests. Long logs go to a file in the scratchpad.
