# Changelog

## 1.0.0 (2026-10-20)

Focus clarified: Ballast is a research and simulation toolkit for fixed-rate stablecoin yield. It holds no keys and sends no
transactions outside a local fork.

- Research results added to the README: out-of-sample backtest over 2025-04-20 → 2026-10-19 and a live snapshot.
- Reports use neutral benchmark targets ("N of M targets met") instead of decision labels; the live report is
  `docs/reports/research-report.md`.
- Address pinning is controlled by `strict_addresses`.
- Paper portfolio keeps lending positions unless another venue of the same protocol pays at least 0.5 pp more.
- Docker Compose stack kept for continuous local collection.

## 0.1.0-dev (2026-10-20)

- Collector (Pendle, DefiLlama, RPC gas, dated futures), NLY calculator and cost model calibrated on Pendle quotes.
- Risk rules, whitelist with on-chain verified addresses, sourced incident catalog.
- Greedy allocator with exposure caps, paper portfolios, fork simulation on Anvil, out-of-sample backtest.
- PostgreSQL storage with Alembic migrations, Celery schedule, Telegram notifications, CI.
