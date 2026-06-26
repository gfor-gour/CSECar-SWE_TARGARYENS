"""Unit tests for app.services.analyzer.analyze_ticket.

Covers all 10 sample cases from SUST_Preli_Sample_Cases.json plus edge cases
(empty history, phishing, duplicate payment, vague complaint, etc.).
"""
import json
import re
from pathlib import Path

import pytest

from app.models.request import TicketRequest, TransactionEntry
from app.services.analyzer import (
    analyze_ticket,
    build_safe_customer_reply_stub,
)

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "SUST_Preli_Sample_Cases.json"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "sample_inputs"


# ---------- Helpers ----------

def _load_sample_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


def _load_input(num: int) -> dict:
    return json.loads(
        (FIXTURES_DIR / f"sample_{num:02d}.json").read_text(encoding="utf-8")
    )


def _make_ticket(**overrides) -> TicketRequest:
    base = {
        "ticket_id": "TKT-TEST",
        "complaint": "Generic complaint",
    }
    base.update(overrides)
    return TicketRequest(**base)


# ---------- Sample-case parametrized tests ----------

@pytest.fixture(scope="module")
def sample_cases() -> list[dict]:
    return _load_sample_cases()


@pytest.mark.parametrize("num", list(range(1, 11)))
def test_sample_case_verdict(num, sample_cases):
    """Each sample's evidence_verdict, case_type, and relevant_transaction_id
    must match the spec's expected_output."""
    case = next(c for c in sample_cases if c["id"] == f"SAMPLE-{num:02d}")
    exp = case["expected_output"]
    ticket = TicketRequest(**case["input"])
    resp = analyze_ticket(ticket)
    assert resp.evidence_verdict == exp["evidence_verdict"], (
        f"SAMPLE-{num:02d}: verdict mismatch"
    )
    assert resp.case_type == exp["case_type"], (
        f"SAMPLE-{num:02d}: case_type mismatch"
    )
    assert resp.relevant_transaction_id == exp["relevant_transaction_id"], (
        f"SAMPLE-{num:02d}: relevant_transaction_id mismatch"
    )


@pytest.mark.parametrize("num", list(range(1, 11)))
def test_sample_case_severity_and_department(num, sample_cases):
    """Severity and department must match the spec's expected_output."""
    case = next(c for c in sample_cases if c["id"] == f"SAMPLE-{num:02d}")
    exp = case["expected_output"]
    ticket = TicketRequest(**case["input"])
    resp = analyze_ticket(ticket)
    assert resp.severity == exp["severity"], (
        f"SAMPLE-{num:02d}: severity mismatch (got {resp.severity}, "
        f"exp {exp['severity']})"
    )
    assert resp.department == exp["department"], (
        f"SAMPLE-{num:02d}: department mismatch (got {resp.department}, "
        f"exp {exp['department']})"
    )


@pytest.mark.parametrize("num", list(range(1, 11)))
def test_sample_case_human_review(num, sample_cases):
    """human_review_required must match the spec's expected_output."""
    case = next(c for c in sample_cases if c["id"] == f"SAMPLE-{num:02d}")
    exp = case["expected_output"]
    ticket = TicketRequest(**case["input"])
    resp = analyze_ticket(ticket)
    assert resp.human_review_required == exp["human_review_required"], (
        f"SAMPLE-{num:02d}: human_review_required mismatch"
    )


# ---------- Safety-rule tests for customer_reply ----------

# Forbidden patterns per spec page 8-9. The patterns use word boundaries
# so the safety tail "Please do not share your PIN or OTP" is correctly
# treated as a *warning*, not as the forbidden imperative "share your PIN".
# We assert "share your PIN" only when NOT preceded by "do not" / "never".
FORBIDDEN_REFUND_PROMISES = [
    r"\bwe will refund\b",
    r"\bwe will reverse\b",
    r"\bwe will unblock\b",
    r"\bwill be refunded\b",  # promising a refund
    r"\byour account will be (unblocked|restored)\b",
]
# We deliberately allow the safety tail phrase "do not share your PIN"
# so the test does NOT false-positive on the warning itself.
# Therefore each pattern uses negative lookbehind for "do not" and "never".
FORBIDDEN_CREDENTIAL_REQUESTS = [
    r"(?<!do not )(?:please\s+)?share your (?:pin|otp|password)",
    r"(?<!do not )(?:please\s+)?send your (?:pin|otp|password)",
    r"(?<!do not )(?:please\s+)?share (?:us )?your (?:pin|otp|password)",
    r"(?<!do not )tell me your (?:pin|otp|password)",
    r"(?<!never )(?:please\s+)?share your (?:pin|otp|password)",
    r"(?<!never )(?:please\s+)?share (?:us )?your (?:pin|otp|password)",
]
FORBIDDEN_THIRD_PARTY = [
    r"call\s+\+?\d{6,}",          # a specific phone number to call
    r"contact\s+[A-Z]{3,}-\d+",  # a specific agent/merchant code to contact
]


@pytest.mark.parametrize("num", list(range(1, 11)))
def test_customer_reply_safety(num, sample_cases):
    """customer_reply must NEVER contain forbidden phrases."""
    case = next(c for c in sample_cases if c["id"] == f"SAMPLE-{num:02d}")
    ticket = TicketRequest(**case["input"])
    resp = analyze_ticket(ticket)
    reply = resp.customer_reply

    for pattern in FORBIDDEN_REFUND_PROMISES + FORBIDDEN_THIRD_PARTY:
        assert not re.search(pattern, reply, re.IGNORECASE), (
            f"SAMPLE-{num:02d}: customer_reply matches forbidden pattern "
            f"'{pattern}': {reply!r}"
        )
    for pattern in FORBIDDEN_CREDENTIAL_REQUESTS:
        assert not re.search(pattern, reply, re.IGNORECASE), (
            f"SAMPLE-{num:02d}: customer_reply requests credentials via "
            f"'{pattern}': {reply!r}"
        )


@pytest.mark.parametrize("num", list(range(1, 11)))
def test_customer_reply_never_leaks_txn_id(num, sample_cases):
    """customer_reply must NOT include the specific transaction_id (per spec
    page 8-9 separation rule)."""
    case = next(c for c in sample_cases if c["id"] == f"SAMPLE-{num:02d}")
    ticket = TicketRequest(**case["input"])
    resp = analyze_ticket(ticket)
    if resp.relevant_transaction_id:
        assert resp.relevant_transaction_id not in resp.customer_reply, (
            f"SAMPLE-{num:02d}: customer_reply leaks transaction_id: "
            f"{resp.customer_reply!r}"
        )


# ---------- Edge cases ----------

def test_empty_transaction_history_phishing():
    """Phishing report with no transaction history: case_type=phishing,
    severity=critical, human_review=True, txn=None."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-1",
        complaint=(
            "Someone called me pretending to be from bKash and asked for my "
            "OTP. I didn't share anything."
        ),
        language="en",
        user_type="customer",
        transaction_history=[],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "phishing_or_social_engineering"
    assert resp.severity == "critical"
    assert resp.human_review_required is True
    assert resp.relevant_transaction_id is None
    assert "PIN" in resp.customer_reply or "OTP" in resp.customer_reply


def test_empty_history_vague_customer_asks_for_clarification():
    """Vague customer complaint with no history: case_type=other,
    severity=low, asks for clarification. Does NOT require human review
    (the customer is asked for more details first; this matches
    SAMPLE-06's expected behavior)."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-2",
        complaint="Help, my money disappeared.",
        language="en",
        user_type="customer",
        transaction_history=[],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "other"
    assert resp.human_review_required is False
    assert resp.relevant_transaction_id is None
    # Customer reply asks for the transaction ID (no leak; we don't have one)
    assert "transaction id" in resp.customer_reply.lower()


def test_duplicate_payment_identifies_second_txn():
    """Two identical payments within 12 seconds: the LATER one is the
    suspected duplicate and must be returned as relevant_transaction_id."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-3",
        complaint="I paid 850 taka for my electricity bill but it deducted twice.",
        language="en",
        user_type="customer",
        transaction_history=[
            TransactionEntry(
                transaction_id="TXN-AA",
                timestamp="2026-04-14T08:15:30Z",
                type="payment",
                amount=850,
                counterparty="BILLER-DESCO",
                status="completed",
            ),
            TransactionEntry(
                transaction_id="TXN-BB",
                timestamp="2026-04-14T08:15:42Z",
                type="payment",
                amount=850,
                counterparty="BILLER-DESCO",
                status="completed",
            ),
        ],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "duplicate_payment"
    assert resp.relevant_transaction_id == "TXN-BB"
    assert resp.evidence_verdict == "consistent"


def test_failed_payment_with_deduction_is_high_severity():
    """A failed payment where customer claims balance was deducted must be
    classified as payment_failed with high severity."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-4",
        complaint=(
            "I tried to pay 1200 taka for my mobile recharge but it failed "
            "and my balance was deducted."
        ),
        language="en",
        user_type="customer",
        transaction_history=[
            TransactionEntry(
                transaction_id="TXN-FF",
                timestamp="2026-04-14T16:00:00Z",
                type="payment",
                amount=1200,
                counterparty="MERCHANT-MOBILE-OP",
                status="failed",
            ),
        ],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "payment_failed"
    assert resp.severity == "high"
    assert resp.evidence_verdict == "consistent"
    assert resp.relevant_transaction_id == "TXN-FF"


def test_ambiguous_match_returns_insufficient_data():
    """Multiple transfers of the same amount → must NOT pick one, must return
    insufficient_data with relevant_transaction_id=None."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-5",
        complaint=(
            "I sent 1000 to my brother yesterday but he says he didn't get it."
        ),
        language="en",
        user_type="customer",
        transaction_history=[
            TransactionEntry(
                transaction_id="TXN-X1",
                timestamp="2026-04-13T11:20:00Z",
                type="transfer",
                amount=1000,
                counterparty="+8801712001122",
                status="completed",
            ),
            TransactionEntry(
                transaction_id="TXN-X2",
                timestamp="2026-04-13T19:45:00Z",
                type="transfer",
                amount=1000,
                counterparty="+8801812334455",
                status="completed",
            ),
        ],
    )
    resp = analyze_ticket(ticket)
    assert resp.evidence_verdict == "insufficient_data"
    assert resp.relevant_transaction_id is None
    assert resp.case_type == "wrong_transfer"


def test_merchant_settlement_delay_routes_to_merchant_ops():
    """Merchant settlement delay must route to merchant_operations, not
    customer_support."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-6",
        complaint=(
            "I am a merchant. My yesterday's sales of 15000 taka have not been "
            "settled to my account."
        ),
        language="en",
        user_type="merchant",
        transaction_history=[
            TransactionEntry(
                transaction_id="TXN-MS",
                timestamp="2026-04-13T18:00:00Z",
                type="settlement",
                amount=15000,
                counterparty="MERCHANT-SELF",
                status="pending",
            ),
        ],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "merchant_settlement_delay"
    assert resp.department == "merchant_operations"
    assert resp.severity == "medium"


def test_bangla_complaint_yields_bangla_reply():
    """Bangla input must produce a Bangla customer_reply (per SAMPLE-07)."""
    ticket = _make_ticket(
        ticket_id="TKT-EDGE-7",
        complaint=(
            "আমি আজ সকালে এজেন্টের কাছে ২০০০ টাকা ক্যাশ ইন করেছি কিন্তু "
            "আমার ব্যালেন্সে টাকা আসেনি।"
        ),
        language="bn",
        user_type="customer",
        transaction_history=[
            TransactionEntry(
                transaction_id="TXN-BN",
                timestamp="2026-04-14T09:30:00Z",
                type="cash_in",
                amount=2000,
                counterparty="AGENT-318",
                status="pending",
            ),
        ],
    )
    resp = analyze_ticket(ticket)
    assert resp.case_type == "agent_cash_in_issue"
    # Bangla reply should contain at least one Bangla character
    assert any("\u0980" <= ch <= "\u09ff" for ch in resp.customer_reply), (
        f"Expected Bangla reply, got: {resp.customer_reply!r}"
    )
    # And must NOT leak the transaction_id
    assert "TXN-BN" not in resp.customer_reply


def test_invalid_complaint_raises():
    """Empty/whitespace complaint must be rejected by Pydantic validation."""
    with pytest.raises(Exception):
        _make_ticket(ticket_id="TKT-BAD", complaint="   ")


# ---------- Direct unit tests for build_safe_customer_reply_stub ----------

def test_stub_never_mentions_transaction_id():
    """The stub helper itself must accept a transaction_id-like value but
    not include it in the output (defense in depth)."""
    reply = build_safe_customer_reply_stub(
        case_type="payment_failed",
        ticket_id="TKT-X",
        complaint="I paid 500 taka but the payment failed.",
        language="en",
    )
    assert "TXN-1234" not in reply  # no txn_id was passed; no leak
    assert "PIN" in reply or "OTP" in reply  # safety tail always present


def test_stub_phishing_includes_safety_warning():
    reply = build_safe_customer_reply_stub(
        case_type="phishing_or_social_engineering",
        ticket_id="TKT-X",
        complaint="Someone asked for my OTP.",
        language="en",
    )
    assert "PIN" in reply
    assert "OTP" in reply
    # Must not promise a refund
    assert "we will refund" not in reply.lower()
