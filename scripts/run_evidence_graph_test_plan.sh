#!/usr/bin/env bash
# D. Evidence Graph Test Plan — bash version
#
# Runs schema/contract, deterministic build, snapshot immutability, and impact
# analysis tests against a running compliance-core API.
#
# Dependencies: curl, jq
# Usage:
#   COMPLIANCE_CORE_URL=http://localhost:8005 ./scripts/run_evidence_graph_test_plan.sh
# For T3-T6 (graph tests):
#   EVIDENCE_GRAPH_ENABLED=true EVIDENCE_GRAPH_DB_DSN=postgresql://... ./scripts/run_evidence_graph_test_plan.sh

set -euo pipefail

BASE="${COMPLIANCE_CORE_URL:-http://localhost:8005}"
BASE="${BASE%/}"
TIMEOUT=30
AUDIT="test_plan_audit"
FW="NIS2"
VER="2024"
ARTIFACT="policy_001"
REQS='["NIS2-Art21-2a","NIS2-Art21-2b"]'
REQ1='["NIS2-Art21-2a"]'

# Strip control chars that break jq (e.g. from OpenAPI descriptions with unescaped newlines)
sanitize_json() {
  if command -v tr >/dev/null 2>&1; then
    tr -d '\000-\037\177' 2>/dev/null || sed 's/[[:cntrl:]]/ /g'
  else
    sed 's/[[:cntrl:]]/ /g'
  fi
}

get() {
  curl -sf --max-time "$TIMEOUT" "$BASE$1"
}

post() {
  curl -sf --max-time "$TIMEOUT" -X POST -H "Content-Type: application/json" -d "$2" "$BASE$1"
}

# Connectivity check
if ! get /v1/health >/dev/null 2>&1; then
  echo "Cannot reach $BASE"; exit 1
fi

graph_ready=false
if [ -n "${EVIDENCE_GRAPH_DB_DSN:-}" ]; then
  case "${EVIDENCE_GRAPH_ENABLED:-}" in
    1|true|yes|on) graph_ready=true ;;
  esac
fi
$graph_ready || echo "Note: EVIDENCE_GRAPH_DB_DSN + EVIDENCE_GRAPH_ENABLED=true needed for T3-T6"

FAILED=""

run_t1() {
  echo -n "T1 "
  O=$(get /openapi.json | sanitize_json)
  S=$(echo "$O" | jq -r '.components.schemas')
  N=$(echo "$S" | jq -r '.GraphNode // .["GraphNode-Input"] // .["GraphNode-Output"] // empty')
  if [ -z "$N" ]; then echo "FAIL: GraphNode schema missing"; return 1; fi
  echo "$N" | jq -e '.properties.node_id or .properties.node_type or .properties.ref_uid' >/dev/null 2>&1 || { echo "FAIL: GraphNode missing required fields"; return 1; }
  echo "$S" | jq -e '.GraphEdge' >/dev/null || { echo "FAIL: GraphEdge schema missing"; return 1; }
  echo "$S" | jq -e '.GraphSnapshot' >/dev/null || { echo "FAIL: GraphSnapshot schema missing"; return 1; }
  echo "PASS: OpenAPI exposes Graph schemas"
}

run_t2() {
  echo -n "T2 "
  HASH=$(printf 'a%.0s' {1..64})
  BODY="{\"node_id\":\"InvalidNodeType:x\",\"node_type\":\"InvalidNodeType\",\"ref_uid\":\"x\",\"properties\":{\"artifact_id\":\"x\",\"source\":\"upload\",\"location\":\"\",\"hash\":\"$HASH\"}}"
  CODE=$(curl -s -o /tmp/t2.json -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "$BODY" "$BASE/v1/graph/validate/node")
  if [ "$CODE" != "422" ]; then
    echo "FAIL: Expected 422, got $CODE"; return 1
  fi
  if ! grep -qiE "node_type|invalid|enum|value" /tmp/t2.json 2>/dev/null; then
    echo "FAIL: 422 detail should mention node_type or invalid"; return 1
  fi
  echo "PASS: Invalid node_type returns 422"
}

run_t3() {
  echo -n "T3 "
  PAY="{\"audit_id\":\"${AUDIT}_t3\",\"framework\":\"$FW\",\"version\":\"$VER\",\"provider\":\"mock\",\"artifact_ids\":[\"$ARTIFACT\"],\"requirement_uids\":$REQS,\"corpus_id\":\"hu_nis2\"}"
  R1=$(post /v1/evaluate "$PAY" | sanitize_json)
  R2=$(post /v1/evaluate "$PAY" | sanitize_json)
  S1=$(echo "$R1" | jq -r '.snapshot_id // empty')
  S2=$(echo "$R2" | jq -r '.snapshot_id // empty')
  [ -z "$S1" ] && { echo "FAIL: snapshot_id expected"; return 1; }
  [ "$S1" != "$S2" ] && { echo "FAIL: Snapshot IDs must match"; return 1; }
  I1=$(get "/v1/graph/artifacts/$ARTIFACT/impact?audit_id=${AUDIT}_t3" | sanitize_json)
  I2=$(get "/v1/graph/artifacts/$ARTIFACT/impact?audit_id=${AUDIT}_t3" | sanitize_json)
  N1=$(echo "$I1" | jq -r '[.nodes[].node_id]|sort|join(",")')
  N2=$(echo "$I2" | jq -r '[.nodes[].node_id]|sort|join(",")')
  [ "$N1" != "$N2" ] && { echo "FAIL: Node IDs differ"; return 1; }
  E1=$(echo "$I1" | jq -r '[.edges[].edge_id]|sort|join(",")')
  E2=$(echo "$I2" | jq -r '[.edges[].edge_id]|sort|join(",")')
  [ "$E1" != "$E2" ] && { echo "FAIL: Edge IDs differ"; return 1; }
  echo "PASS: Deterministic graph build"
}

run_t4() {
  echo -n "T4 "
  PAY="{\"audit_id\":\"${AUDIT}_t4\",\"framework\":\"$FW\",\"version\":\"$VER\",\"provider\":\"mock\",\"artifact_ids\":[\"$ARTIFACT\"],\"requirement_uids\":$REQ1,\"corpus_id\":\"hu_nis2\"}"
  R=$(post /v1/evaluate "$PAY" | sanitize_json)
  FID=$(echo "$R" | jq -r '.findings[0].finding_id // empty')
  [ -z "$FID" ] && { echo "FAIL: No finding"; return 1; }
  EX=$(get "/v1/explain/finding/$FID" | sanitize_json)
  echo "$EX" | jq -e --arg f "Finding:$FID" '[.nodes[]|select(.node_id==$f)]|length>0' >/dev/null 2>&1 || { echo "FAIL: Finding node missing"; return 1; }
  echo "$EX" | jq -e '[.nodes[]|select(.node_type=="Evidence")]|length>0' >/dev/null 2>&1 || { echo "FAIL: Evidence node missing"; return 1; }
  echo "$EX" | jq -e '[.nodes[]|select(.node_type=="Artifact")]|length>0' >/dev/null 2>&1 || { echo "FAIL: Artifact node missing"; return 1; }
  echo "PASS: Explain finding returns expected path"
}

run_t5() {
  echo -n "T5 "
  PAY_A="{\"audit_id\":\"${AUDIT}_t5\",\"framework\":\"$FW\",\"version\":\"$VER\",\"provider\":\"mock\",\"artifact_ids\":[\"$ARTIFACT\"],\"requirement_uids\":$REQ1,\"corpus_id\":\"hu_nis2\"}"
  PAY_B="{\"audit_id\":\"${AUDIT}_t5\",\"framework\":\"$FW\",\"version\":\"$VER\",\"provider\":\"mock\",\"artifact_ids\":[\"$ARTIFACT\"],\"requirement_uids\":$REQS,\"corpus_id\":\"hu_nis2\"}"
  RA=$(post /v1/evaluate "$PAY_A" | sanitize_json)
  SNA=$(echo "$RA" | jq -r '.snapshot_id // empty')
  [ -z "$SNA" ] && { echo "FAIL: snapshot_id expected"; return 1; }
  IA=$(get "/v1/graph/artifacts/$ARTIFACT/impact?snapshot_id=$SNA" | sanitize_json)
  NA=$(echo "$IA" | jq -r '[.nodes[].node_id]|sort|join(",")')
  EA=$(echo "$IA" | jq -r '[.edges[].edge_id]|sort|join(",")')
  RB=$(post /v1/evaluate "$PAY_B" | sanitize_json)
  SNB=$(echo "$RB" | jq -r '.snapshot_id // empty')
  [ "$SNA" = "$SNB" ] && { echo "FAIL: Different inputs must produce different snapshot"; return 1; }
  IA2=$(get "/v1/graph/artifacts/$ARTIFACT/impact?snapshot_id=$SNA" | sanitize_json)
  NA2=$(echo "$IA2" | jq -r '[.nodes[].node_id]|sort|join(",")')
  EA2=$(echo "$IA2" | jq -r '[.edges[].edge_id]|sort|join(",")')
  [ "$NA" != "$NA2" ] && { echo "FAIL: Snapshot A node set changed"; return 1; }
  [ "$EA" != "$EA2" ] && { echo "FAIL: Snapshot A edge set changed"; return 1; }
  echo "PASS: Snapshot immutability"
}

run_t6() {
  echo -n "T6 "
  PAY="{\"audit_id\":\"${AUDIT}_t6\",\"framework\":\"$FW\",\"version\":\"$VER\",\"provider\":\"mock\",\"artifact_ids\":[\"$ARTIFACT\"],\"requirement_uids\":$REQS,\"corpus_id\":\"hu_nis2\"}"
  post /v1/evaluate "$PAY" >/dev/null
  IMP=$(get "/v1/graph/artifacts/$ARTIFACT/impact?audit_id=${AUDIT}_t6" | sanitize_json)
  NF=$(echo "$IMP" | jq '[.nodes[]|select(.node_type=="Finding")]|length')
  NR=$(echo "$IMP" | jq '[.nodes[]|select(.node_type=="Requirement")]|length')
  [ "${NF:-0}" -lt 2 ] 2>/dev/null && { echo "FAIL: Expected >=2 findings, got ${NF:-0}"; return 1; }
  [ "${NR:-0}" -lt 2 ] 2>/dev/null && { echo "FAIL: Expected >=2 requirements, got ${NR:-0}"; return 1; }
  echo "$IMP" | jq -e '.nodes[]|select(.node_type=="Requirement" and .ref_uid=="NIS2-Art21-2a")' >/dev/null 2>&1 || { echo "FAIL: NIS2-Art21-2a missing"; return 1; }
  echo "$IMP" | jq -e '.nodes[]|select(.node_type=="Requirement" and .ref_uid=="NIS2-Art21-2b")' >/dev/null 2>&1 || { echo "FAIL: NIS2-Art21-2b missing"; return 1; }
  echo "PASS: Artifact impact returns findings and requirements"
}

# Run tests (disable set -e for individual test execution)
set +e
run_t1 || FAILED="${FAILED} T1"
run_t2 || FAILED="${FAILED} T2"
if $graph_ready; then
  run_t3 || FAILED="${FAILED} T3"
  run_t4 || FAILED="${FAILED} T4"
  run_t5 || FAILED="${FAILED} T5"
  run_t6 || FAILED="${FAILED} T6"
else
  echo "T3 SKIP (evidence graph not configured)"
  echo "T4 SKIP (evidence graph not configured)"
  echo "T5 SKIP (evidence graph not configured)"
  echo "T6 SKIP (evidence graph not configured)"
fi
set -e

echo ""
if [ -n "${FAILED:-}" ]; then
  echo "Failed:${FAILED}"
  exit 1
fi
echo "All tests passed."
exit 0
