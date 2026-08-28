# Hanni web app

This repository is the web-only version of Hanni. It has four responsibilities:

1. Read content from the configured Discord channel with `USER_AUTH` and store it in Postgres.
2. Serve a searchable, single-page feed at `/` and `/api/feed`.
3. Check allowlisted media URLs directly over HTTP and mark confirmed dead URLs.
4. Recover dead Imgur media, upload a trimmed replacement, and update the related rows.

There is no Discord bot, gateway connection, Discord message sender, or personal `/bias` feed here. Discord is a read-only historical source for ingestion and recovery fallback.

## Local setup

Create the fresh environment from the repository root:

```bash
conda env create -f environment.yml
conda activate hanni-web
cp .env.example .env
```

Fill in the small set of required values in `.env`: `DATABASE_URL`, `USER_AUTH`, and `IMGUR_CLIENT_ID` (plus the source channel IDs if they differ from the defaults). Apply the numbered SQL migrations to the database in order. If you are reusing the existing Heroku Postgres database, leave its data intact and apply only migrations it does not already have; `migrations/table_updates30.sql` adds the web dead-link state.

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

Recovery requires `IMGUR_CLIENT_ID` and `ffmpeg`. It only uploads after direct media validation and never posts a verification message to Discord.

## Background process choices

The `Procfile` supports both Heroku process types:

```text
web:    the FastAPI site
worker: the continuous ingestion/check/recovery scheduler
```

For the smallest first deployment, run one web dyno and set `RUN_BACKGROUND_TASKS=true`. That keeps the site and scheduler in one process. If the site should remain responsive during long ingestion or recovery work, run one web dyno plus one worker dyno and leave `RUN_BACKGROUND_TASKS=false` on the web process. Postgres advisory locks prevent duplicate job runs if both processes briefly overlap.

## Database history

The existing numbered migrations were copied into this repository so the new app can share the current Postgres data. Do not reset or recreate production. See `migrations/README.md` and apply `table_updates30.sql` after the prior schema history.
