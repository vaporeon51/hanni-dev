# Database migrations

The SQL files are the existing Hanni schema history copied into this web-only
repository. For a fresh database, apply `create_tables.sql`, then `roles.sql`,
then `content.sql`, followed by `table_updates.sql` and
`table_updates2.sql` through `table_updates32.sql` in numeric order. For the
existing Heroku Postgres database, apply only migrations it does not already
have. Do not recreate the production database.

`table_updates30.sql` adds the per-URL state used by the web dead-link worker.
It is intentionally separate from the old Discord checker cursor.
`table_updates31.sql` adds the separate user-reported dead-link counter.
`table_updates32.sql` applies the immediate-dead rule to URLs that already
received a confirmed Discord `article` result.
