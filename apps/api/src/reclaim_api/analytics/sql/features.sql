-- Classifier features from the deduplicated transactions view (no tenant identifiers beyond the keyed hash).
SELECT
  transaction_id,
  tenant_key,
  narrative,
  direction,
  amount_minor,
  EXTRACT(DAY FROM booked_at) AS day
FROM transactions
WHERE narrative IS NOT NULL
