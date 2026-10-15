-- Handover milestones (emitted from Prompt 11). Turnaround is computed from these pairs.
SELECT asset_key AS handover_key, event_type, occurred_at
FROM asset_events
WHERE tenant_key = @tenant AND event_type IN ('analytics.handover_requested', 'analytics.handover_released')
  AND DATE(occurred_at) BETWEEN @start AND @end
ORDER BY handover_key, occurred_at
