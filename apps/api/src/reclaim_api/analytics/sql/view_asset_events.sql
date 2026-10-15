-- Deduplicated minimized asset-change events (no labels, amounts or identifiers).
SELECT
  event_id,
  tenant_id AS tenant_key,
  aggregate_id AS asset_key,
  aggregate_version,
  event_type,
  JSON_VALUE(payload, '$.asset_type') AS asset_type,
  JSON_VALUE(payload, '$.change_kind') AS change_kind,
  JSON_VALUE(payload, '$.verification_state') AS verification_state,
  JSON_VALUE(payload, '$.source_date') AS source_date,
  occurred_at,
  publish_time AS ingested_at
FROM analytics_raw
QUALIFY ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY publish_time) = 1
