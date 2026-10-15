# BigQuery analytics (not provisioned)

- `schema.sql`: raw tables partitioned by event date and clustered by tenant key and event type.
- Views and queries: `apps/api/src/reclaim_api/analytics/sql/*.sql` (BigQuery dialect). Locally the
  same files run against the `warehouse` Postgres schema through sqlglot, so they are exercised by
  the test suite; they have not been run on BigQuery itself.
- Ingestion: Pub/Sub topics `transactions` and `analytics` each get a BigQuery subscription
  (use table schema, write metadata) into `*_raw`, with a dead-letter topic whose subscription
  writes `ingest_errors`. The API queries through `BigQueryWarehouse` with typed parameters and a
  default dataset, always filtered by the actor's tenant key.
- Minimization: tenant and aggregate IDs are keyed hashes; payloads are whitelisted
  (`events/envelope.py`). No chat text, document text, names, labels or account numbers.
- Terraform for the dataset, tables, topics and subscriptions comes with Prompt 12.
