#!/usr/bin/env bash
# Focused security suite (Prompt 12). Each group maps to a control in docs/SECURITY.md.
# Runs the existing tests that prove the control; prints one PASS/FAIL line per group.
set -uo pipefail
cd "$(dirname "$0")/.."
T=tests/api
declare -a NAMES GROUPS_
add() { NAMES+=("$1"); GROUPS_+=("$2"); }
add "tenant isolation" "$T/test_records_db.py::test_cross_tenant_reads_are_denied_and_indistinguishable_from_missing $T/test_records_db.py::test_database_refuses_cross_tenant_references $T/test_records_db.py::test_tenant_counts_are_isolated $T/test_chat_db.py::test_other_roles_and_tenants_cannot_chat_or_confirm $T/test_documents_db.py::test_documents_are_owner_scoped $T/test_documents_db.py::test_other_tenants_documents_cannot_be_attached $T/test_claims_db.py::test_other_tenants_cannot_see_or_create_claims"
add "tool authorization" "$T/test_chat_unit.py::test_confirmation_is_not_a_chat_tool $T/test_handover_db.py::test_no_model_or_tool_can_move_a_case $T/test_handover_db.py::test_requester_and_owner_cannot_approve $T/test_handover_db.py::test_only_designated_family_can_request"
add "injection" "$T/test_chat_db.py::test_injection_cannot_reach_another_tenants_asset $T/test_chat_db.py::test_hallucinated_values_are_dropped_not_recorded $T/test_chat_db.py::test_model_output_with_extra_fields_is_invalid_and_commits_nothing $T/test_documents_db.py::test_document_instructions_cannot_invoke_tools $T/test_claims_db.py::test_injected_passage_cannot_widen_what_the_answer_covers"
add "duplicate delivery" "$T/test_chat_db.py::test_retrying_confirmation_creates_exactly_one_version $T/test_chat_db.py::test_concurrent_confirmations_record_once $T/test_events_db.py::test_duplicate_delivery_is_processed_once $T/test_telegram_db.py::test_duplicate_update_does_not_duplicate_draft $T/test_analytics_db.py::test_duplicate_deliveries_do_not_inflate_totals"
add "stale confirmation" "$T/test_chat_db.py::test_stale_confirmation_is_refused $T/test_handover_db.py::test_new_evidence_and_grant_changes_invalidate_approvals"
add "two-reviewer release" "$T/test_handover_db.py::test_held_case_corrected_then_released_by_two_reviewers $T/test_handover_db.py::test_one_reviewer_cannot_approve_twice $T/test_handover_db.py::test_simultaneous_approvals_release_once $T/test_handover_db.py::test_rejected_cases_are_final"
add "scoped PDF" "$T/test_workspace_passport_db.py::test_recipient_export_excludes_hidden_records_and_sections $T/test_workspace_passport_db.py::test_download_checks_permission_at_download_time $T/test_workspace_passport_db.py::test_recipient_cannot_download_a_stale_export $T/test_workspace_passport_db.py::test_tampered_file_is_not_served $T/test_handover_db.py::test_wrong_recipient_and_direct_urls_fail"
add "retrieval grounding" "$T/test_claims_unit.py::test_citation_refs_must_resolve_to_exactly_one_retrieved_section $T/test_claims_unit.py::test_wrong_jurisdiction_unapproved_and_outdated_documents_are_excluded $T/test_claims_db.py::test_chat_policy_question_is_grounded_and_cited $T/test_claims_db.py::test_explanation_steps_must_cite_checklist_sections"
add "cloud identity" "$T/test_cloud_controls_db.py $T/test_events_db.py::test_oidc_requires_the_push_service_account $T/test_events_db.py::test_cloud_outbox_task_accepts_only_the_scheduler_identity"
add "recovery (optional)" "$T/test_recovery_db.py::test_priya_searches_only_inside_her_released_case $T/test_recovery_db.py::test_search_cannot_take_a_name_or_other_terms $T/test_recovery_db.py::test_matches_and_claims_never_touch_inventory_access_or_money $T/test_recovery_db.py::test_priya_claim_is_a_separate_case_and_releases_nothing $T/test_recovery_db.py::test_explanations_that_claim_ownership_relationship_or_invent_numbers_are_dropped $T/test_recovery_unit.py::test_only_an_authorized_synthetic_registry_is_read"

failed=0
for i in "${!NAMES[@]}"; do
  # shellcheck disable=SC2086
  out=$(LLM_PROVIDER=fixture uv run pytest -q ${GROUPS_[$i]} 2>&1 | tail -1)
  if [[ "$out" == *failed* || "$out" == *error* || "$out" != *passed* ]]; then status=FAIL; failed=1; else status=PASS; fi
  printf '%-22s %s  (%s)\n' "${NAMES[$i]}" "$status" "$out"
done
exit $failed
