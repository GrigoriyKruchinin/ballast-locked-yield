---
name: run-backtest
description: Run the Ballast backtest on matured Pendle markets with an honest out-of-sample split and summarize it. Use when evaluating parameters or refreshing research results.
---

1. Confirm data coverage: `make backfill` finished (or `uv run ballast backtest` against a database with history); note gaps.
2. Tier premiums are chosen on the tuning window only; every reported metric comes from the validation window.
3. Run `make backtest` (or `uv run ballast backtest`). The report goes to `docs/reports/backtest-<YYYY-MM-DD>.md`; raw tables stay in
   the database (`backtest_runs`), not in chat.
4. Summarize each benchmark target from `docs/decisions.md`: value, threshold, met / not met; NLY closeness to the threshold,
   cost share of gross yield, lending comparison and incident-adjusted yield.
5. Label results as historical/estimated. Never re-tune on the validation window. If README results change, update them exactly.
