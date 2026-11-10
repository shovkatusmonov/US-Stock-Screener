# Unified US Stock Screener Platform — Integration Plan

## Purpose

Consolidate the existing US Stock Screener data and capabilities into one reliable platform, while preserving all validated historical data and keeping the existing Metriqa production workloads isolated.

## Repository and deployment boundaries

- GitHub source repository: `shovkatusmonov/US-Stock-Screener`.
- Current GitHub default branch at planning time: `main`, commit `d671a8bfbcecdda805f9936c082ccc1a91289603`.
- Modular deployment workspace: `/home/ubuntu/us_screener_platform` on the Metriqa VPS.
- Legacy project directory `/home/ubuntu/us_stock_screener` is an archive/source and must remain untouched.
- Existing production services, databases, scheduled jobs and files on the Metriqa VPS must not be changed by this integration.
- No systemd service, cron job, scheduler, Telegram bot or automated data collection is to be enabled until explicitly approved.
- Use free resources only; do not introduce paid data/API services.

## Verified data migration state

The modular staging database `data/unified_staging.db` was built separately from the original modular database. It contains:

- `market_data_daily`: 1,342,610 rows, 707 distinct symbols, date range 1962-01-02 through 2026-11-06.
- `company_fundamentals`: 171,494 rows imported from the legacy database, exact row comparison passed.
- `sp500_historical_membership`: 670 rows imported, exact row comparison passed.
- `legacy_prices_unmapped`: 869 preserved legacy price rows for AVB (2), EA (1), and KVUE (866); these remain unmapped and must not be discarded.
- SQLite integrity check passed after imports.
- Backups were created before each import operation.

Important: the staging database is not yet promoted to the default/live database. The original modular `data/screener.db` remains separate.

## Current verified application state

- The modular project compiled successfully.
- Its test suite passed: 37 tests.
- `config/settings.py` now supports `SCREENER_DB_PATH`; the default remains `data/screener.db`.
- The environment override was tested and correctly selected `data/unified_staging.db` without changing either database.
- Some modules have hard-coded database paths or call constructors without explicitly passing a database path; all entry points still require a complete integration audit.

## Critical blockers before any backfill or scheduler activation

1. **Database path consistency**
   - Ensure CLI, Telegram bot, scheduler, analytics, scanners, monitoring, loader, universe and backfill all use the same configured database path.
   - Remove hard-coded `data/screener.db` fallbacks where they bypass central configuration.
   - Confirm every read and write path points to the intended database.

2. **Instrument identity**
   - Treat `(symbol, exchange)` as the instrument identity.
   - Remove ambiguous `exchange="US"` wildcard behavior from exact instrument lookups.
   - Audit benchmark aliases (SPY, QQQ, IWM, DIA) and duplicate symbol/exchange records before normalizing them.
   - Do not infer exchange mappings for unknown codes such as `F` without evidence.

3. **Historical price preservation**
   - Historical imports and backfills must not overwrite validated existing rows by default.
   - Replace unsafe `INSERT OR REPLACE` behavior with an explicit conflict policy.
   - Keep incremental refresh/correction behavior separate from historical backfill behavior.
   - Preserve null values as null; do not convert missing financial or price values to zero.

4. **Universe synchronization safety**
   - Do not mark instruments delisted unless all required exchange-directory sources have downloaded, parsed, and passed non-empty validation.
   - A failed or partial source must abort the delisting phase.
   - Review legacy-universe migration so it cannot trigger false delistings.
   - Reconcile the current difference between `universe` (13,769 rows) and `instruments` (13,239 rows).

5. **Provider and backfill correctness**
   - Distinguish provider/network failure from a valid empty-history response; failures must never create `NO_DATA` records.
   - Add resumable, rate-limited batches and persistent progress.
   - Audit the definition of `ALL_TIME_COMPLETE`; do not claim complete history based only on first/last returned dates.
   - Include historical delisted instruments through a separate, validated eligibility path.
   - Protect the low-memory VPS with bounded workers, batch sizes and resource checks.

6. **Fundamentals and corporate actions**
   - Integrate the imported fundamentals table into the modular application with explicit schema and null semantics.
   - Add validated support for dividends, splits, ticker changes, listing/delisting history and company metadata.
   - Keep source/provenance and retrieval timestamps for every imported dataset.

7. **Validation**
   - Add tests for exchange-specific identity, conflict preservation, partial directory failures, provider errors, resumability and database-path propagation.
   - Run unit/integration tests against isolated temporary databases.
   - Compare row counts, key uniqueness, date ranges, table schemas and SQLite integrity before/after every migration.
   - Do not promote staging to the default database until the validation report passes and explicit approval is received.

## Immediate next milestone

Complete a read-only audit of the modular deployment workspace and produce a file-by-file implementation plan. The audit must not modify databases, start services, perform backfills, or run universe synchronization.

## Acceptance criteria

- One configured database path is used consistently by every application entry point.
- All 1,342,610 validated daily price rows and all imported legacy tables remain intact.
- The 869 unmapped price rows remain preserved until safe instrument mappings are established.
- No false delistings or destructive price overwrites are possible.
- Backfill is resumable, rate-limited, and distinguishes provider errors from true no-data results.
- Tests and integrity checks pass before any live activation.
- Existing Metriqa production workloads and the legacy source directory remain untouched.
