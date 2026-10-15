-- Monthly inflows and outflows per currency. Currencies are never combined.
-- Months come from the booking time, not ingestion time, so late events land in their month.
SELECT
  DATE_TRUNC(DATE(booked_at), MONTH) AS month,
  currency,
  SUM(IF(direction = 'credit', amount_minor, 0)) AS inflow_minor,
  SUM(IF(direction = 'debit', amount_minor, 0)) AS outflow_minor,
  COUNT(*) AS transactions
FROM transactions
WHERE tenant_key = @tenant AND DATE(booked_at) BETWEEN @start AND @end
GROUP BY month, currency
ORDER BY month, currency
