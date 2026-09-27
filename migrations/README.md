# Database migrations

The SQL files are the existing Hanni schema history copied into this web-only
repository. For a fresh database, apply `create_tables.sql`, then `roles.sql`,
then `content.sql`, followed by `table_updates.sql` and
`table_updates2.sql` through `table_updates39.sql` in numeric order. For the
existing Heroku Postgres database, apply only migrations it does not already
have. Do not recreate the production database.

`table_updates30.sql` adds the per-URL state used by the web dead-link worker.
It is intentionally separate from the old Discord checker cursor.
`table_updates31.sql` adds the separate user-reported dead-link counter.
`table_updates32.sql` applies the immediate-dead rule to URLs that already
received a confirmed Discord `article` result.
`table_updates33.sql` adds indexes for exact-post and legacy posting-burst
collection lookups.

`table_updates39.sql` creates the live idol rating, daily budget, and weekly
snapshot tables, then copies legacy ratings. The new live Elo starts at the
old **displayed** score; original raw ratings and historical snapshots remain
in the legacy tables. New idols start at 1200. There is one rating per idol,
with fractional precision and rounding only for display. Any recorded matchup
qualifies an idol for a numbered rank; no contributor threshold is required.

Migration 39 contains no catalog rows. `scripts/sync_sorter_idols.py` is the
only catalog inserter; it uses `catalog_rating_rows()` to resolve the current
catalog to one row per person and never resets ratings or matchup counts.

Rollout order (not yet run for this draft):

1. Stop old vote writers / snapshot workers for the migration window.
2. Apply migration 39 against the intended database.
3. Run `python scripts/sync_sorter_idols.py --apply`. It verifies every catalog
   identity exists before committing. `--check` performs that check without
   writes; no flag is an offline dry run.
4. Deploy the new app/catalog and start the updated worker. Do not restart old
   vote writers: `role_info.global_elo` is now an archived baseline.

This draft replaces the earlier, unapplied version of migration 39. It is
rerunnable against its own final schema without resetting live ratings, but
is not an upgrade script for the abandoned intermediate draft schema.

For future catalog additions, sync before publishing the catalog. Preserve
existing `leaderboard_id` values. Merging two existing identities requires
an explicit data migration rather than silently changing IDs.

Visitor pair ballots and budgets store SHA-256 cookie digests for new votes.
The existing snapshot worker cleanup removes rows older than two days when
it runs; schedule that worker to maintain retention. Pre-migration raw-cookie
pair rows expire through the same cleanup. There is no lifetime contributor
ledger. Cookie clearing still creates a new anonymous identity; hashing does
not prevent that. Historical matchup totals retain the old counting rules,
while new repeats and budget-exhausted votes do not increment counts.

The Groups board still covers the legacy Discord-mapped member population.
It averages the top three live idol scores with the existing group eligibility
rule, without applying the old display shrinkage a second time.

Database regression tests require an isolated local PostgreSQL database:
`HANNI_TEST_DATABASE_URL='host=127.0.0.1 dbname=hanni_test ...' python -m pytest tests/test_idol_elo_db.py`.
They create and drop their own test schemas and never use `DATABASE_URL`.
Validation completed: the focused catalog/API/PostgreSQL suite passed against
an isolated local database, including migrations, catalog sync, concurrent
vote limits, and atomic snapshots. All 12 sorter tests also passed. The broader
Python suite has three pre-existing homepage/scroll UI failures, reproduced
on the unchanged baseline. Before deployment, verify migration 39 and catalog
coverage on the target database with `python scripts/sync_sorter_idols.py --check`.
