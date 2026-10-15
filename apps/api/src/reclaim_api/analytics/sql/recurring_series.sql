-- Months in which each account/category recurring series occurred (gaps are found from these).
SELECT
  account_token,
  feed_category AS category,
  currency,
  DATE_TRUNC(DATE(booked_at), MONTH) AS month,
  COUNT(*) AS occurrences,
  MAX(amount_minor) AS max_amount_minor
FROM transactions
WHERE tenant_key = @tenant AND DATE(booked_at) BETWEEN @start AND @end
  AND feed_category IN ('investment_contribution', 'insurance_premium', 'loan_payment', 'income')
GROUP BY account_token, category, currency, month
ORDER BY account_token, category, month
