-- Each debit against the same account's earlier debits (mean, sample stddev, count).
SELECT * FROM (
  SELECT
    transaction_id,
    account_token,
    currency,
    feed_category AS category,
    booked_at,
    amount_minor,
    AVG(amount_minor) OVER w AS prior_mean,
    STDDEV_SAMP(amount_minor) OVER w AS prior_sd,
    COUNT(*) OVER w AS prior_n
  FROM transactions
  WHERE tenant_key = @tenant AND direction = 'debit' AND DATE(booked_at) <= @end
  WINDOW w AS (PARTITION BY account_token, currency ORDER BY booked_at ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
) AS scored
WHERE DATE(booked_at) BETWEEN @start AND @end
ORDER BY booked_at
