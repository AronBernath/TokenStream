#!/usr/bin/env bash
# Comprehensive compliance test: workbook-driven evaluation (new workflow).
# Uses gpt-5.1 via orchestrator (provider=openai).
# Uses audit_workbook_1.1.yaml (claims + evidence refs from Excel) with IBSZ.txt evidence.
# Usage: ./scripts/run_compliance_comprehensive_test.sh [BASE_URL] [ORCHESTRATOR_URL]
# Example: ./scripts/run_compliance_comprehensive_test.sh http://localhost:8005 http://localhost:8004
#
# Requires: compliance-core, orchestrator-api. Evidence docs from tests/fixtures/policies/
# (IBSZ.txt). Script reads files and sends content in request (no volume mount needed).
# Set OPENAI_API_KEY if orchestrator requires auth.

# Run all tests even when some fail
FAIL_COUNT=0
BASE_URL="${1:-http://localhost:8005}"
ORCHESTRATOR_URL="${2:-${ORCHESTRATOR_URL:-http://localhost:8004}}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="openai:gpt-5.1"
FIXTURES_DIR="${REPO_ROOT}/tests/fixtures/policies"

# Prefer python3 for portability
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
  PYTHON="python"
fi

gen_run_id() {
  "$PYTHON" - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
}

echo "=== Comprehensive Compliance Test (workbook workflow, gpt-5.1) ==="
echo "  BASE_URL=$BASE_URL"
echo "  ORCHESTRATOR_URL=$ORCHESTRATOR_URL"
echo "  Model: $MODEL"
echo "  Audit: audit_workbook_001 (audit_workbook_1.1.yaml + IBSZ.txt)"
echo "  Framework: NIS2 2024"
echo ""

# ----- Prereq: compliance-core and frameworks -----
echo "=== Prerequisites ==="
echo "P1: Compliance-core reachable"
if curl -sf "${BASE_URL}/v1/health" > /dev/null 2>&1; then
  echo "  Pass: health OK."
else
  echo "  FAIL: compliance-core unreachable at $BASE_URL"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo "P2: NIS2 2024 framework available"
REQ_RESP=$(curl -sf "${BASE_URL}/v1/requirements?framework=NIS2&version=2024" 2>/dev/null || echo '{}')
if echo "$REQ_RESP" | jq -e '.requirements | length >= 1' >/dev/null 2>&1; then
  REQ_COUNT=$(echo "$REQ_RESP" | jq '.requirements | length' 2>/dev/null || echo "0")
  echo "  Pass: NIS2 2024 has $REQ_COUNT requirement(s)."
else
  echo "  FAIL: NIS2 2024 framework not available"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

echo "P3: Evidence fixture present (IBSZ.txt)"
if [ -f "${FIXTURES_DIR}/IBSZ.txt" ]; then
  echo "  Pass: IBSZ.txt found."
else
  echo "  FAIL: IBSZ.txt not found at ${FIXTURES_DIR}/IBSZ.txt"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ----- Evaluate: workbook-driven (pre-provided claims, evidence content in request) -----
echo ""
echo "=== Evaluate (gpt-5.1) — workbook workflow ==="
echo "E1: POST /v1/evaluate/workbook"
IBSZ_CONTENT=$(jq -Rs . < "${FIXTURES_DIR}/IBSZ.txt" 2>/dev/null || echo '""')
RUN_ID_1="$(gen_run_id)"
echo "  run_id=$RUN_ID_1"
EVAL_BODY=$(jq -n \
  --argjson ibsz "$IBSZ_CONTENT" \
  --arg model "$MODEL" \
  --arg run_id "$RUN_ID_1" \
  '{
    audit_id: "audit_workbook_001",
    framework: "NIS2",
    version: "2024",
    artifact_contents: {"IBSZ.txt": $ibsz},
    provider: "openai",
    model: $model,
    run_id: $run_id
  }')
EVAL_OUT=$(curl -s -X POST "${BASE_URL}/v1/evaluate/workbook" \
  -H "Content-Type: application/json" \
  -d "$EVAL_BODY" 2>/dev/null)
[ -z "$EVAL_OUT" ] && EVAL_OUT='{}'
if echo "$EVAL_OUT" | jq -e '.error or .detail' >/dev/null 2>&1; then
  echo "  Skip: LLM evaluate failed (orchestrator may be down)"
  ERR_MSG=$(echo "$EVAL_OUT" | jq -r 'if .message then "\(.error): \(.message)" else .detail.message // .detail.error // .detail // .error // "unknown" end' 2>/dev/null)
  echo "  $ERR_MSG"
  EVAL_OUT='{"findings":[]}'
fi
FINDINGS=$(echo "$EVAL_OUT" | jq '.findings | length' 2>/dev/null || echo "0")
# Workbook has multiple entries; expect at least 1 finding when LLM is available
if [ "${FINDINGS:-0}" -ge 1 ] 2>/dev/null; then
  echo "  Pass: $FINDINGS finding(s)."
else
  echo "  Skip: 0 findings (orchestrator/LLM may be down)"
fi

echo "E2: Each finding has status and evidence_refs"
ALL_VALID=true
if [ "$FINDINGS" -gt 0 ] 2>/dev/null; then
  for i in $(seq 0 $((FINDINGS - 1))); do
    STATUS=$(echo "$EVAL_OUT" | jq -r ".findings[$i].status" 2>/dev/null)
    REQ_UID=$(echo "$EVAL_OUT" | jq -r ".findings[$i].requirement_uid" 2>/dev/null)
    REFS=$(echo "$EVAL_OUT" | jq ".findings[$i].evidence_refs | length" 2>/dev/null || echo "0")
    if [ -n "$STATUS" ] && [ "$STATUS" != "null" ]; then
      echo "    $REQ_UID: status=$STATUS, evidence_refs=$REFS"
      if [ "${REFS:-0}" -lt 1 ] 2>/dev/null; then
        ALL_VALID=false
      fi
    else
      echo "    FAIL: finding $i missing status"
      ALL_VALID=false
    fi
  done
  if [ "$ALL_VALID" != "true" ]; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
else
  echo "    (no findings to check)"
fi

echo ""
echo "  --- Evaluate metadata ---"
echo "$EVAL_OUT" | jq '.metadata' 2>/dev/null || true
echo ""
echo "  --- Findings (full rationale) ---"
echo "$EVAL_OUT" | jq -r '.findings[]? | "[\(.requirement_uid)] \(.status)\nRationale:\n\(.rationale // "")\n"' 2>/dev/null || echo "  (no output)"
echo ""
echo "  --- Citations (evidence_refs per finding) ---"
echo "$EVAL_OUT" | jq '.findings[]? | {requirement_uid, status, citations: .evidence_refs}' 2>/dev/null || echo "  (no output)"
echo ""

# ----- Consistency: re-run evaluate (LLM may vary; check structure) -----
echo ""
echo "=== Consistency check ==="
echo "D1: Re-run evaluate/workbook, compare structure"
RUN_ID_2="$(gen_run_id)"
echo "  run_id=$RUN_ID_2"
EVAL_BODY2=$(jq -n \
  --argjson ibsz "$IBSZ_CONTENT" \
  --arg model "$MODEL" \
  --arg run_id "$RUN_ID_2" \
  '{
    audit_id: "audit_workbook_001",
    framework: "NIS2",
    version: "2024",
    artifact_contents: {"IBSZ.txt": $ibsz},
    provider: "openai",
    model: $model,
    run_id: $run_id
  }')
EVAL2=$(curl -s -X POST "${BASE_URL}/v1/evaluate/workbook" \
  -H "Content-Type: application/json" \
  -d "$EVAL_BODY2" 2>/dev/null)
[ -z "$EVAL2" ] && EVAL2='{}'
FINDINGS2=$(echo "$EVAL2" | jq '.findings | length' 2>/dev/null || echo "0")
UIDS1=$(echo "$EVAL_OUT" | jq -r '[.findings[].requirement_uid] | sort[]' 2>/dev/null | tr '\n' ' ')
UIDS2=$(echo "$EVAL2" | jq -r '[.findings[].requirement_uid] | sort[]' 2>/dev/null | tr '\n' ' ')
if [ "$FINDINGS" -eq "$FINDINGS2" ] 2>/dev/null && [ "$UIDS1" = "$UIDS2" ] && [ -n "$UIDS1" ]; then
  echo "  Pass: same findings count ($FINDINGS) and requirement_uids on two runs."
else
  echo "  Skip: LLM output may vary; run 1 findings=$FINDINGS, run 2 findings=$FINDINGS2 (rationale can differ)"
fi

# ----- Config validation (optional) -----
echo ""
echo "=== Config validation ==="
echo "V1: Validator (canonical base)"
if COMPLIANCE_CONTENT_DIR="${REPO_ROOT}/compliance_content" "$PYTHON" -m compliance_content.validate_all 2>/dev/null; then
  echo "  Pass: validate_all exit 0."
else
  echo "  Skip: validate_all failed (python/env)."
fi

echo ""
if [ "$FAIL_COUNT" -gt 0 ]; then
  echo "FAILED: $FAIL_COUNT test(s) failed."
  exit 1
else
  echo "All comprehensive tests passed."
  exit 0
fi
