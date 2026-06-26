"""Smoke test for analyze_ticket against all 10 sample cases.

Asserts verdict, case_type, and relevant_transaction_id match expected_output.
Prints PASS/FAIL summary. Per spec, other fields may vary but the three
primary structural fields are the acceptance gate.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.models.request import TicketRequest
from app.services.analyzer import analyze_ticket

ROOT = Path(__file__).parent
CASES_PATH = ROOT / "SUST_Preli_Sample_Cases.json"


def main():
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = data["cases"]

    passed = 0
    failed = 0
    for case in cases:
        cid = case["id"]
        inp = case["input"]
        exp = case["expected_output"]
        ticket = TicketRequest(**inp)
        resp = analyze_ticket(ticket)

        ok_verdict = resp.evidence_verdict == exp["evidence_verdict"]
        ok_case = resp.case_type == exp["case_type"]
        ok_txn = resp.relevant_transaction_id == exp["relevant_transaction_id"]

        status = "PASS" if (ok_verdict and ok_case and ok_txn) else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1
        print(
            f"{status} {cid}: verdict={resp.evidence_verdict} "
            f"(exp {exp['evidence_verdict']}) | "
            f"case_type={resp.case_type} "
            f"(exp {exp['case_type']}) | "
            f"txn={resp.relevant_transaction_id} "
            f"(exp {exp['relevant_transaction_id']}) | "
            f"severity={resp.severity} dept={resp.department} "
            f"hr={resp.human_review_required} conf={resp.confidence}"
        )
        if not (ok_verdict and ok_case and ok_txn):
            print(f"   expected_output: {json.dumps(exp, indent=2)[:500]}")

    print(f"\n{passed}/{passed + failed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())