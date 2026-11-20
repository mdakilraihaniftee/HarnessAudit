#!/usr/bin/env bash
# test_resume_campaign.sh — campaign interruption + resume, and round-to-round
# feedback flow, with the deterministic mock attacker (no GPU needed).
#   1. run rounds 1-2, 2. resume to round 4 with the same campaign id,
#   3. check rounds 1..4 exist exactly once, the harness state was restored, and
#      every round after the first was generated from stored memory of earlier rounds.
# Usage: scripts/tests/test_resume_campaign.sh
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../local_qwen/lib.sh"
ha_require_python
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
T=multi_agent/tasks/finance/financial_planning/fin-t1.yaml
CID=resume$$
common=(--task "$T" --backend mock --arm B6 --output-dir "$TMP/out" --memory-db "$TMP/mem.sqlite")
"$HA_PYTHON" -m multi_agent.attacks campaign "${common[@]}" --campaign-id "$CID" --rounds 2 --dry-run > "$TMP/a.log" 2>&1 \
  || { cat "$TMP/a.log"; echo "FAIL  initial campaign"; exit 1; }
"$HA_PYTHON" -m multi_agent.attacks resume "${common[@]}" --campaign-id "$CID" --rounds 4 --dry-run > "$TMP/b.log" 2>&1 \
  || { cat "$TMP/b.log"; echo "FAIL  resume"; exit 1; }
grep -q "Resuming $CID from round 3" "$TMP/b.log" && echo "PASS  resumed from round 3" || { echo "FAIL  resume did not start at round 3"; exit 1; }
"$HA_PYTHON" - "$TMP/mem.sqlite" "$CID" "$TMP/out/campaign_$CID" <<'PY'
import json, sqlite3, sys
from pathlib import Path
db = sqlite3.connect(sys.argv[1]); db.row_factory = sqlite3.Row
rows = db.execute("SELECT a.round_index, c.candidate_json FROM attempts a JOIN candidates c "
                  "USING(candidate_id) WHERE a.campaign_id=? ORDER BY a.round_index",
                  (sys.argv[2],)).fetchall()
rounds = [r["round_index"] for r in rows]
assert rounds == [1, 2, 3, 4], rounds
print("PASS  rounds 1..4 recorded exactly once")
meta = [json.loads(r["candidate_json"])["generation_metadata"] for r in rows]
assert meta[0]["memory_records_retrieved"] == 0
for m in meta[1:]:
    assert m["memory_records_retrieved"] >= 1 and m["memory_attempt_ids"], m
print("PASS  rounds 2-4 were generated from stored memory of earlier rounds "
      f"(retrieved {[m['memory_records_retrieved'] for m in meta]})")
out = Path(sys.argv[3])
p3 = (out / "prompts" / "round_0003.txt").read_text()
hist = json.loads(p3.split("REQUEST_CONTEXT_JSON:\n", 1)[1])["history"]
assert {h["round_index"] for h in hist} >= {1, 2}, hist
print("PASS  round-3 prompt (after resume) contains feedback from rounds 1 and 2")
report = json.loads((out / "report.json").read_text())
assert [a["round"] for a in report["attempts"]] == [1, 2, 3, 4]
status = db.execute("SELECT status FROM campaigns WHERE campaign_id=?", (sys.argv[2],)).fetchone()[0]
assert status == "completed", status
print("PASS  report regenerated and campaign marked completed")
PY
