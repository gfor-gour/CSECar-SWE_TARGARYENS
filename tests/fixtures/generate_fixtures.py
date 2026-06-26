"""Generate per-case input and expected-output fixture files from
SUST_Preli_Sample_Cases.json.

Writes:
  - tests/fixtures/sample_inputs/sample_NN.json  (input)
  - tests/fixtures/sample_outputs/sample_NN.json (expected_output)
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "SUST_Preli_Sample_Cases.json"
IN_DIR = ROOT / "tests" / "fixtures" / "sample_inputs"
OUT_DIR = ROOT / "tests" / "fixtures" / "sample_outputs"
IN_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

data = json.loads(CASES.read_text(encoding="utf-8"))
for case in data["cases"]:
    cid = case["id"]  # e.g. SAMPLE-01
    num = cid.split("-")[1]  # "01"

    in_path = IN_DIR / f"sample_{num}.json"
    in_path.write_text(
        json.dumps(case["input"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {in_path}")

    out_path = OUT_DIR / f"sample_{num}.json"
    out_path.write_text(
        json.dumps(case["expected_output"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out_path}")
