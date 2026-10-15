-- Deduplicated transactions. Pub/Sub delivers at least once, so the raw table can hold the
-- same event_id more than once; every analytical query reads this view, never the raw table.
SELECT
  event_id,
  tenant_id AS tenant_key,
  JSON_VALUE(payload, '$.account_token') AS account_token,
  JSON_VALUE(payload, '$.transaction_id') AS transaction_id,
  CAST(JSON_VALUE(payload, '$.amount_minor') AS INT64) AS amount_minor,
  JSON_VALUE(payload, '$.direction') AS direction,
  JSON_VALUE(payload, '$.currency') AS currency,
  JSON_VALUE(payload, '$.feed_category') AS feed_category,
  JSON_VALUE(payload, '$.narrative') AS narrative,
  occurred_at AS booked_at,
  publish_time AS ingested_at
FROM transactions_raw
QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY publish_time) = 1
