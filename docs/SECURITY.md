# Security controls and how they are tested

`make security` runs the focused suite (`scripts/security.sh`) against the local database and
prints one PASS/FAIL line per control. `make check` runs everything. Last recorded result:
see STATUS.md.

| Control | What is enforced | Tests |
|---|---|---|
| Tenant isolation | Every query is scoped from the actor, never from request IDs. Cross-tenant reads return 404, so they look the same as missing records. Composite foreign keys stop cross-tenant references in the database itself. | `test_records_db`, `test_chat_db`, `test_documents_db`, `test_claims_db` |
| Tool authorization | Confirmation, handover approval and release are API actions for authorized humans. No model output or chat tool can trigger them. Reviewers cannot be requester or owner. | `test_chat_unit::test_confirmation_is_not_a_chat_tool`, `test_handover_db` |
| Injection | Messages, documents and retrieved passages are data. Model output is schema-checked (extra fields are rejected), and every value must appear in the owner's own words. | `test_chat_db`, `test_documents_db::test_document_instructions_cannot_invoke_tools`, `test_claims_db::test_injected_passage_cannot_widen_what_the_answer_covers` |
| Duplicate delivery | Idempotency keys and row locks on confirmation. Per-consumer `processed_events`. Telegram `update_id` dedupe. Analytics dedup views. | `test_chat_db`, `test_events_db`, `test_telegram_db`, `test_analytics_db` |
| Stale confirmation | Optimistic `base_version` on proposals. Handover approvals are tied to the current evidence and grant versions. | `test_chat_db::test_stale_confirmation_is_refused`, `test_handover_db::test_new_evidence_and_grant_changes_invalidate_approvals` |
| Two-reviewer release | Two distinct reviewers on the current version, under a row lock. Rejection is final. | `test_handover_db` |
| Scoped PDF | Recipient exports filter records and sections when generated. Permission is re-checked at download, and stale or tampered files are refused. | `test_workspace_passport_db`, `test_handover_db::test_wrong_recipient_and_direct_urls_fail` |
| Retrieval grounding | Applicability (approved, in force, jurisdiction, institution, asset type) is checked in code. Citations must resolve to exactly one retrieved section. | `test_claims_unit`, `test_claims_db` |
| Cloud identity | Dev tokens are refused in cloud mode. Identity Platform tokens are checked for issuer, and an account links by verified email once, then by subject only. Worker endpoints accept one service account each. | `test_cloud_controls_db`, `test_events_db` |
| Recovery (optional) | The search takes no names; the subject comes from the actor or a released case. Only an authorized synthetic registry is read. Matches never become assets or grants. AI explanations claiming ownership, relationship or probability are dropped. | `test_recovery_db`, `test_recovery_unit` |

## Not covered (gaps)

- No external penetration test, dependency audit or container scan has been run.
- Cloud IAM, OIDC push and Identity Platform have only been tested with mocked token verification, never against real Google endpoints.
- No rate limiting beyond Cloud Run `max_instances`; add Cloud Armor before a public launch.
