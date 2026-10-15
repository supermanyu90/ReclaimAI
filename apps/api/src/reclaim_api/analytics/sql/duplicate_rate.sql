-- Raw deliveries versus distinct events in the window.
SELECT
  COUNT(*) AS deliveries,
  COUNT(DISTINCT event_id) AS distinct_events
FROM transactions_raw
WHERE tenant_id = @tenant AND DATE(occurred_at) BETWEEN @start AND @end
