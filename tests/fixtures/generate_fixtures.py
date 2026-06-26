"""Generate per-case input fixture files from SUST_Preli_Sample_Cases.json.

Writes tests/fixtures/sample_inputs/sample_NN.json for each case.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "SUST_Preli_Sample_Cases.json"
OUT = ROOT / "tests" / "fixtures" / "sample_inputs"
OUT.mkdir(parents=True, exist_ok=True)

data = json.loads(CASES.read_text(encoding="utf-8"))
for case in data["cases"]:
    cid = case["id"]  # e.g. SAMPLE-01
    num = cid.split("-")[1]  # "01"
    out_path = OUT / f"sample_{num}.json"
    out_path.write_text(
        json.dumps(case["input"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out_path}")
