# Hanni web app

This repository is the web-only version of Hanni. It has four responsibilities:

1. Read content from the configured Discord channel with `USER_AUTH` and store it in Postgres.
2. Serve browsing views at `/feed`, `/sets`, and `/scroll`, plus the sorter and leaderboard.
3. Check whether media URLs embed in Discord and mark confirmed failures as dead.
4. Recover dead Imgur media, upload a trimmed replacement, and update the related rows.

Signed Discord CDN and media-proxy attachment URLs are intentionally excluded from ingestion and feed selection because their query-string signatures expire. Historical rows remain in Postgres but are not served or checked.

There is no Discord bot, gateway connection, Discord message sender, or personal `/bias` feed here. Discord is a read-only historical source for ingestion and recovery fallback.

## Local setup

Create the fresh environment from the repository root:

```bash
conda env create -f environment.yml
conda activate hanni-web
cp .env.example .env
```

Fill in the small set of required values in `.env`: `DATABASE_URL`, `USER_AUTH`, `IMGUR_CLIENT_ID`, `DISCORD_DEAD_LINK_WEBHOOK_URL`, and `DISCORD_REVIVAL_WEBHOOK_URL` (plus the source channel IDs if they differ from the defaults). Use two private channels: the first webhook receives routine dead-link probes, and the second receives newly uploaded revival links for dead-on-arrival validation. Webhook URLs are secrets and must never be committed. Apply the numbered SQL migrations to the database in order. If you are reusing the existing Heroku Postgres database, leave its data intact and apply only migrations it does not already have; `migrations/table_updates30.sql` adds the web dead-link state and `migrations/table_updates31.sql` adds user-reported dead-link counts.

The worker's intervals, batch sizes, connection-pool sizing, media allowlist, and recovery limits have safe code defaults. They can be overridden later with Heroku config vars when needed, but do not need to live in local `.env`.

Run the site locally:

```bash
uvicorn src.web.app:app --reload --port 8000
```

The first web page is at http://127.0.0.1:8000. Background jobs can be run manually while developing:

```bash
python -m src.worker ingest
python -m src.worker dead-links
python -m src.worker recovery
```

Historical content can be inspected with the resumable backfill command. It is
a read-only dry run unless `--apply` is supplied:

```bash
python scripts/backfill_content.py --max-pages 5
python scripts/backfill_content.py --apply --max-pages 5
python scripts/backfill_content.py --apply
```

The backfill replays the channel chronologically through the live classifier,
fills Discord provenance on unambiguous legacy rows, and inserts missing
continuations beginning January 1, 2025. Apply-mode progress is written atomically after every page to
the ignored local file `.content-backfill-state.json`; it never moves the live
cursor in `update_log` and requires no additional database table. Rerun the
same command after interruption to resume. Dry-run mode never writes content
or a checkpoint.

The dead-link worker applies `MIN_CONTENT_AGE`, posts each eligible candidate URL to its private channel, waits up to 15 seconds for Discord's embed, and records the old `article`-embed behavior as dead. Messages remain in the channel as an audit trail. A missing embed is unknown rather than dead; an explicit `article` embed marks every row for that URL dead immediately and posts a notice in the same channel. URLs whose cards are actually revealed in the web feed enter a process-local, de-duplicated FIFO priority queue capped at 100 items. The checker is serial: it takes one queued URL when available, otherwise one regular database-sweep URL, processes it fully, waits two seconds, and repeats. Individual database URLs retain a five-minute minimum recheck interval. The queue is shared with the scheduler in the one-dyno `RUN_BACKGROUND_TASKS=true` deployment. Without `DISCORD_DEAD_LINK_WEBHOOK_URL`, the job skips safely instead of applying a different definition of dead.

Recovery requires `IMGUR_CLIENT_ID`, `DISCORD_REVIVAL_WEBHOOK_URL`, and `ffmpeg`. Candidates use the same `MIN_CONTENT_AGE` eligibility rule as the public feed. After direct media validation, each new URL is posted to the separate revival channel. An explicit Discord `article` embed advances the failed derivative generation and leaves the source dead; a delayed embed is accepted like the old Tsuki pipeline because no embed is not proof of failure. Those messages remain in the channel.

## Background process choices

The `Procfile` supports both Heroku process types:

```text
web:    the FastAPI site
worker: the continuous ingestion/check/recovery scheduler
```

For the smallest first deployment, run one web dyno and set `RUN_BACKGROUND_TASKS=true`. That keeps the site and scheduler in one process. If the site should remain responsive during long ingestion or recovery work, run one web dyno plus one worker dyno and leave `RUN_BACKGROUND_TASKS=false` on the web process. Postgres advisory locks prevent duplicate job runs if both processes briefly overlap.

## Database history

The existing numbered migrations were copied into this repository so the new app can share the current Postgres data. Do not reset or recreate production. See `migrations/README.md` and apply `table_updates30.sql` after the prior schema history.

Idol-label review lives at `/disambiguation`, with no navigation link or password.
The queue ranks exact Discord/Goyangi sets by unresolved wrong-idol reports,
unique image count, then newest upload. Dead links and completed images leave the
queue. Each set opens as a photo wall with lazy previews and per-photo idol
choices. Select every idol present, explicitly choose none, or leave a photo
unsaved for later. Loaded sets stay in memory while navigating back to the queue.
Direct images avoid metadata lookups and use the proxy if hotlinking fails.
Videos use the feed/set player setup and same-origin proxy with Range support.
Saving uses
the existing bot semantics: mark URL assignments disambiguated, clear wrong-idol
reports on retained labels, and suppress other labels at the report threshold
without deleting their rows or changing dead-link reports. Admin previews include
reported labels. “Mark as broken” marks all assignments of the current URL dead
and leaves the existing recovery process available. No new migration is
needed (existing migrations 27 and 41 provide the review flag and set key).

## Page and URL conventions

Public searches use `q` in page URLs, native form names, and API requests.
`sort` controls ordering on feed and sets; `kind` selects the leaderboard tab.
`collection` identifies a set opened from the feed. Keep page-specific keys
only where they have meaning; scroll always samples randomly.

Use `static/site-navigation.js` for reading, validating, building, and updating
page URLs. Searches and discrete filter/tab changes push history entries;
live typing replaces the current entry so Back does not step through each
keystroke. Pagination stays out of browser history. Every page reads its URL
on initial load and Back/Forward. Sorter ranking/session links retain their
existing hash format, and private selections and playback preferences remain
in local storage.
