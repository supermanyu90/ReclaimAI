-- Latest prediction per transaction for one tenant, joined to the transaction for display.
SELECT
  t.transaction_id, t.account_token, t.narrative, t.direction, t.amount_minor, t.currency, t.booked_at,
  p.predicted_category, p.confidence, p.abstained, p.source, p.model_version, p.predicted_at
FROM transactions AS t
JOIN (
  SELECT * FROM transaction_predictions
  WHERE tenant_key = @tenant
  QUALIFY ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY predicted_at DESC) = 1
) AS p ON p.transaction_id = t.transaction_id
WHERE t.tenant_key = @tenant
ORDER BY p.abstained DESC, p.confidence, t.booked_at DESC
LIMIT 200
