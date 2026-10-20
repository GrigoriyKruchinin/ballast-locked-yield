---
name: run-backtest
description: Run the Ballast backtest on matured Pendle markets and the paper-portfolio evaluation, with an honest out-of-sample split. Use when evaluating thresholds or preparing the Go/No-Go report.
---

1. Confirm data coverage: which matured markets have history, which do not (state gaps explicitly).
2. Split time into a tuning window and a validation window. Thresholds are chosen only on tuning data; all reported metrics come from validation data.
3. Run the backtest (command defined in `Makefile`, e.g. `make backtest`). Results go to the DB and to `docs/reports/backtest-<YYYY-MM-DD>.md`; raw tables to files, not chat.
4. Report per Go/No-Go criterion from `docs/decisions.md`: value, threshold, pass/fail. Include NLY distribution, gas+slippage share of gross yield,
   weekly deployable capital share, parking comparison, and incident-adjusted yield per tier.
5. Label results as historical/estimated. Never re-tune on the validation window.
