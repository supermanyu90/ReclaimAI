-- Latest known version of each asset; counted as stale when its source date is over a year old.
SELECT
  COUNT(*) AS assets,
  COUNTIF(source_date IS NULL) AS without_source_date,
  COUNTIF(CAST(source_date AS DATE) < DATE_SUB(@as_of, INTERVAL 365 DAY)) AS stale
FROM (
  SELECT asset_key, source_date
  FROM asset_events
  WHERE tenant_key = @tenant AND event_type = 'analytics.asset_changed' AND change_kind <> 'close'
  QUALIFY ROW_NUMBER() OVER (PARTITION BY asset_key ORDER BY aggregate_version DESC) = 1
) AS latest
