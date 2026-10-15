-- Messages the warehouse sink rejected (schema errors), by topic and reason.
SELECT topic, reason, COUNT(*) AS errors
FROM ingest_errors
WHERE DATE(received_at) BETWEEN @start AND @end
GROUP BY topic, reason
ORDER BY errors DESC
