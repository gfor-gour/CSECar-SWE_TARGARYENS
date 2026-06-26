import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.request import TicketRequest, TransactionEntry
from app.models.response import TicketResponse

# ---------- Constants ----------

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

TIME_KEYWORDS = {
    "today", "আজ",
    "yesterday", "গতকাল",
    "this morning", "আজ সকালে",
    "around 2pm", "দুপুরে",
    "just now", "এইমাত্র",
}

# Map complaint intent phrases to transaction types
TYPE_KEYWORDS = {
    "transfer": [
        "sent", "transfer", "পাঠিয়েছি", "পাঠালাম", "ট্রান্সফার", "send money",
        "transferred",
    ],
    "payment": [
        "paid", "pay", "bill", "recharge", "payment", "বিল", "রিচার্জ",
        "পেমেন্ট", "পেমেন্ট করেছি", "pay korechi",
    ],
    "cash_in": [
        "cash in", "ক্যাশ ইন", "deposit", "জমা",
    ],
    "cash_out": [
        "cash out", "উঠিয়েছি", "উঠেছি", "তুলেছি",
    ],
    "settlement": [
        "settlement", "সেটেলমেন্ট", "settle",
    ],
}

# Phrases that imply a generic transfer/send even without the word "transfer"
GENERIC_SEND_HINTS = ["send", "sent", "পাঠিয়েছি", "পাঠালাম"]


# ---------- Low-level helpers ----------

def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, convert Bangla digits to ASCII."""
    if not text:
        return ""
    t = text.lower().strip()
    t = t.translate(BANGLA_DIGITS)
    t = re.sub(r"\s+", " ", t)
    return t


def extract_numbers(text: str) -> list[float]:
    """
    Pull numeric AMOUNTS out of a complaint string.
    Handles: '5000', '5,000', '5k', '৫০০০', '5000 taka'.
    Filters out phone-number-like digit runs (long contiguous digits or
    sequences immediately following '+', 'no', or after a long-digit prefix).
    """
    if not text:
        return []
    norm = normalize_text(text)
    found: list[float] = []
    # Match number-with-commas OR plain digits, optionally followed by 'k'.
    # The plain-digit branch must be greedy: match ALL consecutive digits.
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+|\d+)(k)?", norm):
        raw = m.group(1).replace(",", "")
        # Filter phone-number-like runs: 10+ contiguous digits is almost
        # certainly a phone number, not an amount.
        if len(raw) >= 10:
            continue
        # Filter digit-after-'+' (e.g., "+8801719876543" -> "8801719876543" is 13 digits)
        start = m.start()
        if start > 0 and norm[start - 1] == "+":
            continue
        val = float(raw)
        if m.group(2):
            val *= 1000
        if val <= 0:
            continue
        # Skip very small numbers (< 100) unless they look like amounts
        # (followed by 'taka', 'tk', 'k', or 'টাকা').
        if val < 100:
            tail = norm[m.end():m.end() + 30]
            kw = ("taka", "tk", "টাকা", "rupee", "bdt")
            if not any(k in tail for k in kw):
                continue
        found.append(val)
    return found


def parse_complaint_time_to_date(text: str, now: Optional[datetime] = None) -> tuple[str, Optional[datetime]]:
    """
    Resolve coarse time references to a UTC date.
    Returns (label, anchor_datetime_or_None).
    """
    norm = normalize_text(text)
    now = now or datetime.now(timezone.utc)
    today = now.date()

    if "today" in norm or "আজ" in norm:
        return "today", datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    if "yesterday" in norm or "গতকাল" in norm:
        d = today - timedelta(days=1)
        return "yesterday", datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if "this morning" in norm or "সকালে" in norm:
        return "morning", None
    if "around 2pm" in norm or "দুপুরে" in norm:
        return "afternoon", None
    if "just now" in norm or "এইমাত্র" in norm:
        return "recent", None
    return "unknown", None


def parse_txn_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp safely; return None on failure."""
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def hour_in_window(tx_dt: datetime, label: str) -> bool:
    h = tx_dt.hour
    if label == "morning":
        return h < 12
    if label == "afternoon":
        return 12 <= h < 17
    return True


def amounts_close(a: float, b: float, pct: float = 0.01) -> bool:
    """True if a and b are within pct (default 1%) of each other."""
    if a == 0 and b == 0:
        return True
    base = max(abs(a), abs(b))
    return abs(a - b) <= base * pct


# ---------- Component 1: Transaction Matcher ----------

def _detect_intent_type(complaint_norm: str) -> Optional[str]:
    """Return the most likely transaction type implied by the complaint text."""
    priority = ["settlement", "cash_out", "cash_in", "payment", "transfer"]
    for t in priority:
        for kw in TYPE_KEYWORDS[t]:
            if kw in complaint_norm:
                return t
    for kw in GENERIC_SEND_HINTS:
        if kw in complaint_norm:
            return "transfer"
    return None


def _amount_matches(amount: float, txn: TransactionEntry, pct: float = 0.01) -> bool:
    return amounts_close(amount, float(txn.amount), pct=pct)


def _is_on_date(tx_dt: datetime, anchor: datetime) -> bool:
    return tx_dt.date() == anchor.date()


def _score_transaction(
    txn: TransactionEntry,
    complaint_norm: str,
    complaint_amounts: list[float],
    time_label: str,
    time_anchor: Optional[datetime],
    intent_type: Optional[str],
) -> int:
    score = 0
    tx_dt = parse_txn_timestamp(txn.timestamp)
    amount_hit = any(_amount_matches(a, txn) for a in complaint_amounts)

    if amount_hit:
        score += 5
    if intent_type and txn.type == intent_type:
        score += 3
    if tx_dt is not None:
        if time_label in ("today", "yesterday") and time_anchor is not None:
            if _is_on_date(tx_dt, time_anchor):
                score += 2
        elif time_label in ("morning", "afternoon") and hour_in_window(tx_dt, time_label):
            score += 2
        elif time_label == "recent":
            score += 1
    return score


def find_relevant_transaction(
    complaint: str,
    transactions: list[TransactionEntry],
) -> Optional[TransactionEntry]:
    """
    Identify the single transaction the complaint is about.
    Returns None if zero or multiple transactions match equally well.
    """
    if not transactions:
        return None

    norm = normalize_text(complaint)
    complaint_amounts = extract_numbers(complaint)
    time_label, time_anchor = parse_complaint_time_to_date(complaint)
    intent_type = _detect_intent_type(norm)

    scored: list[tuple[int, TransactionEntry]] = []
    for txn in transactions:
        s = _score_transaction(
            txn, norm, complaint_amounts, time_label, time_anchor, intent_type,
        )
        scored.append((s, txn))

    candidates = [(s, t) for s, t in scored if s > 0]
    if not candidates:
        return None

    def _sort_key(item):
        s, t = item
        ts = parse_txn_timestamp(t.timestamp)
        ts_epoch = ts.timestamp() if ts else -1e18
        return (-s, -ts_epoch)

    candidates.sort(key=_sort_key)
    top_score = candidates[0][0]
    top_tier = [t for s, t in candidates if s == top_score]
    if len(top_tier) > 1:
        return None

    return top_tier[0]

# ---------- Component 2: Evidence Verdict ----------

# Confidence thresholds
HUMAN_REVIEW_THRESHOLD = 0.70
HIGH_CONFIDENCE = 0.85

# Window (seconds) for "duplicate" detection
DUPLICATE_WINDOW_SECONDS = 60

# Patterns that signal a wrong-recipient transfer
WRONG_RECIPIENT_HINTS = [
    "wrong number", "wrong person", "wrong account",
    "ভুল নম্বর", "ভুল ব্যক্তি", "ভুল অ্যাকাউন্ট",
    "didn't get it", "did not get", "didn't receive", "did not receive",
    "he says", "she says", "brother", "sister", "doesn't have",
]

# Patterns for duplicate / double-charge
DUPLICATE_HINTS = [
    "twice", "double", "deducted twice", "deducted two times",
    "two times", "দুইবার", "দুই বার", "ডাবল",
]

# Patterns for failed transaction
FAILED_HINTS = [
    "failed", "didn't go through", "did not go through",
    "ব্যর্থ", "হয়নি", "success হয়নি",
]

# Patterns for cash-out agent dispute
AGENT_DISPUTE_HINTS = [
    "agent", "এজেন্ট", "counter", "কাউন্টার",
]


def _has_any_hint(norm: str, hints: list[str]) -> bool:
    return any(h in norm for h in hints)


def _find_duplicate_pair(
    transactions: list[TransactionEntry],
    intent_type: Optional[str],
    complaint_amounts: list[float],
) -> Optional[TransactionEntry]:
    """
    If two transactions of the same amount/type to the same counterparty
    occurred within DUPLICATE_WINDOW_SECONDS, return the LATER one as the
    suspected duplicate.
    """
    if len(transactions) < 2:
        return None

    # Only meaningful for payment or cash_in type complaints
    if intent_type not in ("payment", "cash_in", None):
        return None

    parsed: list[tuple[datetime, TransactionEntry]] = []
    for t in transactions:
        dt = parse_txn_timestamp(t.timestamp)
        if dt is not None:
            parsed.append((dt, t))
    parsed.sort(key=lambda x: x[0])

    target_amount = complaint_amounts[0] if complaint_amounts else None

    for i in range(len(parsed) - 1):
        dt1, t1 = parsed[i]
        dt2, t2 = parsed[i + 1]
        if (dt2 - dt1).total_seconds() > DUPLICATE_WINDOW_SECONDS:
            continue
        if t1.counterparty and t2.counterparty and t1.counterparty != t2.counterparty:
            continue
        # Same amount check
        if not amounts_close(float(t1.amount), float(t2.amount)):
            continue
        # If complaint mentions an amount, both must match it
        if target_amount is not None and not (
            amounts_close(target_amount, float(t1.amount))
            and amounts_close(target_amount, float(t2.amount))
        ):
            continue
        return t2  # the later one is the suspected duplicate

    return None


def _find_recent_to_counterparty(
    transactions: list[TransactionEntry],
    complaint_amounts: list[float],
) -> Optional[TransactionEntry]:
    """
    When multiple transfers to the same counterparty exist and the complaint
    mentions an amount, pick the most recent one matching the amount.
    Used for SAMPLE-02-style cases.
    """
    if not complaint_amounts:
        return None
    target = complaint_amounts[0]
    matching = [
        t for t in transactions
        if t.type == "transfer" and amounts_close(target, float(t.amount))
    ]
    if not matching:
        return None

    def _ts(t):
        dt = parse_txn_timestamp(t.timestamp)
        return dt.timestamp() if dt else -1e18

    matching.sort(key=_ts, reverse=True)
    # If there's a clear most-recent (later than the rest by > 1 second), return it
    if len(matching) == 1:
        return matching[0]
    if len(matching) > 1:
        return matching[0]  # best-effort: latest
    return None


def determine_evidence_verdict(
    complaint: str,
    transactions: list[TransactionEntry],
    matched: Optional[TransactionEntry],
) -> tuple[str, list[str], float]:
    """
    Returns (verdict, reason_codes, confidence).
    verdict is one of the three spec values:
        'consistent' | 'inconsistent' | 'insufficient_data'
    """
    norm = normalize_text(complaint)
    amounts = extract_numbers(complaint)
    intent_type = _detect_intent_type(norm)
    time_label, time_anchor = parse_complaint_time_to_date(complaint)
    reason_codes: list[str] = []

    # --- Phishing/SE: no transaction history is consistent (not inconsistent) ---
    # But we'll only treat phishing as 'insufficient_data' on txn evidence;
    # the case_type classifier handles the category.
    if not transactions:
        reason_codes.append("no_transaction_history")
        return "insufficient_data", reason_codes, 0.50

    # --- Duplicate case: data SUPPORTS a duplicate-payment claim ---
    dup = _find_duplicate_pair(transactions, intent_type, amounts)
    if dup is not None and _has_any_hint(norm, DUPLICATE_HINTS):
        reason_codes.append("duplicate_detected")
        reason_codes.append("amount_match")
        return "consistent", reason_codes, 0.92

    # --- Failed transaction: data SUPPORTS the customer's claim of failure ---
    failed_txns = [t for t in transactions if t.status == "failed"]
    if failed_txns and _has_any_hint(norm, FAILED_HINTS):
        reason_codes.append("failed_status_match")
        reason_codes.append("amount_match")
        return "consistent", reason_codes, 0.90

    # --- Clean match via matcher ---
    if matched is not None:
        score = _score_transaction(
            matched, norm, amounts, time_label, time_anchor, intent_type,
        )

        # INCONSISTENT: amount claimed in complaint differs from matched txn > 5%
        if amounts:
            target = amounts[0]
            pct_diff = abs(target - float(matched.amount)) / max(target, 1.0)
            if pct_diff > 0.05:
                reason_codes.append("amount_mismatch")
                return "inconsistent", reason_codes, 0.70

        # INCONSISTENT: customer claims 'failed' but matched txn is completed
        if _has_any_hint(norm, FAILED_HINTS) and matched.status == "completed":
            reason_codes.append("status_contradicts_claim")
            return "inconsistent", reason_codes, 0.72

        # INCONSISTENT: customer claims 'completed/received' but matched txn failed
        # Only fire on strong success words. Avoid common false positives like
        # "have not received" (negative) or "settlement received" (settlement).
        strong_success = ["successfully", "successfully transferred",
                          "সফলভাবে", "সফল হয়েছে"]
        if matched.status == "failed" and any(h in norm for h in strong_success):
            reason_codes.append("status_contradicts_claim")
            return "inconsistent", reason_codes, 0.72

        # INCONSISTENT: wrong-transfer claim but same counterparty appears 2+ times
        if _has_any_hint(norm, WRONG_RECIPIENT_HINTS) and matched.type == "transfer":
            cp = matched.counterparty
            same_count = sum(
                1 for t in transactions
                if t.type == "transfer" and t.counterparty == cp
            )
            if same_count >= 2:
                reason_codes.append("established_recipient_pattern")
                return "inconsistent", reason_codes, 0.68

        # CONSISTENT
        if score >= 5:
            reason_codes.append("amount_match")
            if intent_type and matched.type == intent_type:
                reason_codes.append("type_match")
            if time_label in ("today", "yesterday") and time_anchor is not None:
                tx_dt = parse_txn_timestamp(matched.timestamp)
                if tx_dt and _is_on_date(tx_dt, time_anchor):
                    reason_codes.append("date_match")
            return "consistent", reason_codes, 0.88 if score >= 8 else 0.78

        reason_codes.append("weak_match")
        return "insufficient_data", reason_codes, 0.55

    # --- No matcher hit, but complaint has amount: try duplicate inference ---
    if amounts and dup is not None:
        reason_codes.append("inferred_duplicate")
        return "consistent", reason_codes, 0.70

    # --- Repeated transfers to same counterparty (e.g. SAMPLE-02 fallback) ---
    # Only fire inconsistent when ALL matching transfers go to the SAME
    # counterparty (truly established recipient). If transfers are spread
    # across multiple counterparties (e.g. SAMPLE-08), return
    # insufficient_data because we cannot identify which is the recipient.
    fallback = _find_recent_to_counterparty(transactions, amounts)
    if fallback is not None:
        matching_transfers = [
            t for t in transactions
            if t.type == "transfer" and amounts_close(
                amounts[0], float(t.amount)
            ) if amounts
        ]
        unique_counterparties = {t.counterparty for t in matching_transfers}
        if (
            _has_any_hint(norm, WRONG_RECIPIENT_HINTS)
            and len(unique_counterparties) == 1
            and len(matching_transfers) >= 2
        ):
            reason_codes.append("established_recipient_pattern")
            return "inconsistent", reason_codes, 0.62
        if len(unique_counterparties) > 1:
            reason_codes.append("ambiguous_recipients")
            return "insufficient_data", reason_codes, 0.45
        reason_codes.append("inferred_recent_transfer")
        return "consistent", reason_codes, 0.65

    # --- Nothing to go on ---
    reason_codes.append("no_matching_transaction")
    return "insufficient_data", reason_codes, 0.35


# ---------- Component 3: Case Classifier ----------
#
# Spec (page 7-8 of the problem statement) defines EXACTLY 8 case_type values:
#   wrong_transfer | payment_failed | refund_request | duplicate_payment |
#   merchant_settlement_delay | agent_cash_in_issue |
#   phishing_or_social_engineering | other
#
# Anything that doesn't match a specific rule falls through to "other".

PHISHING_HINTS = [
    "otp", "pin", "password", "ওটিপি", "পিন",
    "called me", "কেউ ফোন করেছে", "কেউ ফোন", "ফোন করেছে",
    "account will be blocked", "verify your account",
    "share your", "claiming to be", "bkash called", "bkaş called",
    "নকল", "প্রতারণা", "scam", "fraud",
]

REFUND_HINTS = [
    "refund", "return my money", "money back", "changed my mind",
    "রিফান্ড", "ফেরত", "টাকা ফেরত", "ফেরত দিন",
]

MERCHANT_HINTS = [
    "merchant", "settlement", "সেটেলমেন্ট", "settle",
    "মার্চেন্ট", "পেমেন্ট পাইনি",
]

AGENT_CASHIN_HINTS = [
    "cash in", "ক্যাশ ইন", "ক্যাশ-ইন", "এজেন্ট", "agent",
    "জমা", "deposit",
]


def classify_case_type(
    complaint: str,
    transactions: list[TransactionEntry],
    matched: Optional[TransactionEntry],
    evidence_verdict: Optional[str] = None,
) -> str:
    """
    Return one of the 8 spec case_type values.
    Rule priority (highest first), all rules use spec keywords:
        1. phishing_or_social_engineering  (any safety/SE signal)
        2. duplicate_payment               (dup-pair found + hint)
        3. payment_failed                  (failed txn + hint)
        4. wrong_transfer                  (transfer + wrong-recipient hint)
        5. merchant_settlement_delay       (merchant/settlement keyword)
        6. agent_cash_in_issue             (cash_in + agent + not-received hint)
        7. refund_request                  (refund keyword, no system failure)
        8. other                           (fallback)
    """
    norm = normalize_text(complaint)
    reason_codes: list[str] = []

    # 1. PHISHING / SOCIAL ENGINEERING — always wins
    if _has_any_hint(norm, PHISHING_HINTS):
        return "phishing_or_social_engineering"

    # 2. DUPLICATE PAYMENT — duplicate pair in history + duplicate keyword
    if _has_any_hint(norm, DUPLICATE_HINTS):
        amounts = extract_numbers(complaint)
        intent_type = _detect_intent_type(norm)
        dup = _find_duplicate_pair(transactions, intent_type, amounts)
        if dup is not None:
            return "duplicate_payment"

    # 3. PAYMENT FAILED — failed txn + failed keyword
    if _has_any_hint(norm, FAILED_HINTS):
        if matched is not None and matched.status == "failed":
            return "payment_failed"
        if any(t.status == "failed" for t in transactions):
            return "payment_failed"
        # Even without a matched failed txn, the hint is strong
        intent_type = _detect_intent_type(norm)
        if intent_type == "payment":
            return "payment_failed"

    # 4. WRONG TRANSFER — transfer + wrong-recipient keyword
    if _has_any_hint(norm, WRONG_RECIPIENT_HINTS):
        intent_type = _detect_intent_type(norm)
        if intent_type == "transfer":
            return "wrong_transfer"
        if matched is not None and matched.type == "transfer":
            return "wrong_transfer"
        # If we have a transfer in history and an amount, lean wrong_transfer
        if any(t.type == "transfer" for t in transactions):
            return "wrong_transfer"

    # 5. REFUND REQUEST — checked BEFORE merchant_settlement_delay because
    # "refund" / "money back" combined with a merchant counterparty is a
    # refund_request, not a settlement delay.
    if _has_any_hint(norm, REFUND_HINTS):
        if evidence_verdict != "inconsistent":
            return "refund_request"

    # 6. MERCHANT SETTLEMENT DELAY
    if _has_any_hint(norm, MERCHANT_HINTS):
        return "merchant_settlement_delay"

    # 7. AGENT CASH-IN ISSUE — cash_in + agent + non-receipt
    not_received_hints = ["not received", "আসেনি", "পাইনি", "দেখছি না",
                         "balance আসেনি", "ব্যালেন্সে আসেনি"]
    if (_has_any_hint(norm, AGENT_CASHIN_HINTS) and
            _has_any_hint(norm, not_received_hints)):
        return "agent_cash_in_issue"
    if matched is not None and matched.type == "cash_in":
        if matched.counterparty.upper().startswith("AGENT"):
            if matched.status in ("pending", "failed"):
                return "agent_cash_in_issue"

    # 8. Default
    return "other"


# ---------- Component 4: Department Routing ----------
#
# Spec (page 8) lists exactly 7 departments:
#   customer_support | dispute_resolution | payments_ops |
#   merchant_operations | agent_operations | fraud_risk
# (plus a 7th: "agent_operations" is listed alongside merchant_operations and fraud_risk.
#  Reviewing the PDF carefully, the 7th is "agent_operations".)
#
# Mapping rules (priority order):
#   phishing/SE               → fraud_risk
#   wrong_transfer            → dispute_resolution
#   payment_failed            → payments_ops
#   duplicate_payment         → payments_ops
#   refund_request            → customer_support
#   merchant_settlement_delay → merchant_operations
#   agent_cash_in_issue       → agent_operations
#   other                     → customer_support (default intake)


def route_department(
    case_type: str,
    user_type: Optional[str] = None,
) -> str:
    """
    Return one of the 7 spec department values.
    Routing is driven by case_type; user_type is a tie-breaker
    (e.g. merchant complaints without a specific case_type still go to
    merchant_operations).
    """
    if case_type == "phishing_or_social_engineering":
        return "fraud_risk"
    if case_type == "wrong_transfer":
        return "dispute_resolution"
    if case_type in ("payment_failed", "duplicate_payment"):
        return "payments_ops"
    if case_type == "refund_request":
        return "customer_support"
    if case_type == "merchant_settlement_delay":
        return "merchant_operations"
    if case_type == "agent_cash_in_issue":
        return "agent_operations"
    # Fallback: if a merchant user has a generic complaint, route to merchant ops
    if user_type == "merchant":
        return "merchant_operations"
    return "customer_support"


# ---------- Component 5: Severity Classification ----------
#
# Spec: low | medium | high | critical
#
# Rules (priority order, first match wins):
#   phishing/SE                         → critical
#   agent_cash_in_issue + pending/failed → high
#   payment_failed + balance_deducted    → high
#   wrong_transfer (no prior pattern)     → high
#   wrong_transfer + established pattern  → medium
#   duplicate_payment                    → high
#   payment_failed (clean)               → medium
#   merchant_settlement_delay            → medium
#   refund_request                       → low
#   other (vague)                        → low
#   insufficient_data + low confidence    → low


def classify_severity(
    case_type: str,
    verdict: str,
    matched: Optional[TransactionEntry],
    confidence: float,
    complaint: str,
) -> str:
    """Return one of: low | medium | high | critical."""
    norm = normalize_text(complaint)

    # CRITICAL: phishing / social engineering is always critical
    if case_type == "phishing_or_social_engineering":
        return "critical"

    # HIGH: agent cash-in with pending or failed status
    if case_type == "agent_cash_in_issue":
        if matched is not None and matched.status in ("pending", "failed"):
            return "high"
        return "medium"

    # HIGH: payment failed AND customer claims balance was deducted
    if case_type == "payment_failed":
        deducted_hints = ["deducted", "কাটা", "কেটে নিয়েছে", "balance was deducted",
                         "cut koreche", "কেটে গেছে"]
        if _has_any_hint(norm, deducted_hints):
            return "high"
        return "medium"

    # HIGH: duplicate payment (financial impact confirmed)
    if case_type == "duplicate_payment":
        return "high"

    # HIGH/MEDIUM: wrong transfer
    if case_type == "wrong_transfer":
        if verdict == "inconsistent":
            return "medium"
        # Ambiguous match (insufficient_data) for a wrong-transfer claim
        # is medium — we cannot confirm the dispute yet.
        if verdict == "insufficient_data":
            return "medium"
        # Clean wrong-transfer dispute
        return "high"

    # MEDIUM: merchant settlement delay
    if case_type == "merchant_settlement_delay":
        return "medium"

    # LOW: refund request (no system failure)
    if case_type == "refund_request":
        return "low"

    # LOW: vague / other
    if verdict == "insufficient_data" and confidence < 0.6:
        return "low"

    # Default fallback
    return "low"


# ---------- Component 6: Human Review Decision ----------


def requires_human_review(
    case_type: str,
    severity: str,
    verdict: str,
    confidence: float,
    has_transactions: bool,
    user_type: Optional[str] = None,
    reason_codes: Optional[list[str]] = None,
    matched_txn_present: bool = False,
) -> bool:
    """
    Return True if a human agent must review this ticket before action.

    Rules (priority order):
      - phishing/SE                                          → True (always)
      - verdict == inconsistent                              → True
      - case_type == agent_cash_in_issue                     → True
      - case_type == duplicate_payment                       → True (financial
        reversal needs human; SAMPLE-10 spec)
      - case_type == wrong_transfer + matched_txn_present    → True
        (dispute can be initiated; SAMPLE-01 spec)
      - case_type == wrong_transfer + insufficient_data      → False
        (ambiguous match — ask customer for clarification first;
         SAMPLE-08 spec)
      - case_type == refund_request + matched_txn_present    → True
        (refund adjudication needs human approval)
      - case_type == refund_request + no matched txn         → False
        (handled automatically by customer_support guidance;
         SAMPLE-04 spec)
      - severity == critical (non-phishing)                  → True
      - wrong_transfer + established_recipient_pattern       → True
      - otherwise                                             → False
        (payment_failed, merchant_settlement_delay handled by ops
         automatically; vague complaints ask for clarification first)
    """
    if case_type == "phishing_or_social_engineering":
        return True
    if verdict == "inconsistent":
        return True
    if case_type == "agent_cash_in_issue":
        return True
    if case_type == "duplicate_payment":
        return True
    if case_type == "wrong_transfer":
        return matched_txn_present
    if case_type == "refund_request":
        return matched_txn_present
    if severity == "critical":
        return True
    if reason_codes and "established_recipient_pattern" in reason_codes:
        return True
    return False


# ---------- Component 7: Agent Summary ----------


def build_agent_summary(
    ticket_id: str,
    matched: Optional[TransactionEntry],
    case_type: str,
    verdict: str,
    reason_codes: list[str],
    complaint: str,
) -> str:
    """
    Concise 1-2 sentence summary for the human agent.
    Includes the transaction_id when known (internal-only field).
    """
    txn_id = matched.transaction_id if matched is not None else None
    amount = float(matched.amount) if matched is not None else None
    counterparty = matched.counterparty if matched is not None else None
    status = matched.status if matched is not None else None

    def _fmt_amount() -> str:
        return f"{amount:.0f} BDT" if amount is not None else "an unspecified amount"

    # Case-type-specific phrasing
    if case_type == "phishing_or_social_engineering":
        return (
            "Customer reports an unsolicited contact requesting credentials or "
            "claiming to represent the company. Likely social engineering attempt."
        )

    if case_type == "wrong_transfer":
        if verdict == "inconsistent" and "established_recipient_pattern" in reason_codes:
            return (
                f"Customer claims {txn_id} ({_fmt_amount()} to {counterparty}) was "
                f"a wrong transfer, but transaction history shows an established "
                f"recipient pattern, which contradicts the claim."
            )
        if txn_id:
            return (
                f"Customer reports sending {_fmt_amount()} via {txn_id} to "
                f"{counterparty}, which they now believe was the wrong recipient."
            )
        return "Customer claims a recent transfer was sent to the wrong recipient."

    if case_type == "payment_failed":
        return (
            f"Customer reports {txn_id} ({_fmt_amount()}, status={status}) "
            f"failed but balance was deducted. Requires payments operations "
            f"investigation."
        )

    if case_type == "refund_request":
        return (
            f"Customer requests refund of {_fmt_amount()} for {txn_id} "
            f"(merchant payment). Not a service failure."
        )

    if case_type == "duplicate_payment":
        return (
            f"Customer reports duplicate payment. Two identical {_fmt_amount()} "
            f"transactions exist; {txn_id} is the suspected duplicate."
        )

    if case_type == "merchant_settlement_delay":
        return (
            f"Merchant reports settlement {txn_id} ({_fmt_amount()}) is "
            f"delayed beyond the standard window. Status: {status}."
        )

    if case_type == "agent_cash_in_issue":
        return (
            f"Customer reports cash-in via {counterparty} ({txn_id}, "
            f"{_fmt_amount()}) not reflected in balance. Status: {status}."
        )

    # Vague / other
    return (
        "Customer reports a concern about their account without sufficient "
        "detail to identify a specific transaction."
    )


# ---------- Component 8: Recommended Next Action ----------


def build_next_action(
    case_type: str,
    severity: str,
    verdict: str,
    has_transactions: bool,
    matched: Optional[TransactionEntry],
    complaint: str,
) -> str:
    """One concrete next step for the handling agent."""
    txn_id = matched.transaction_id if matched is not None else None

    if case_type == "phishing_or_social_engineering":
        return (
            "Escalate to fraud_risk team immediately. Confirm to customer that "
            "the company never asks for OTP. Log the reported number for fraud "
            "pattern analysis."
        )

    if case_type == "wrong_transfer":
        if verdict == "inconsistent":
            return (
                f"Flag {txn_id or 'the transaction'} for human review. Verify "
                f"with the customer whether this was genuinely a wrong transfer "
                f"given the established transaction pattern with this recipient."
            )
        return (
            f"Verify {txn_id} details with the customer and initiate the "
            f"wrong-transfer dispute workflow per policy."
        )

    if case_type == "payment_failed":
        return (
            f"Investigate {txn_id} ledger status. If balance was deducted on a "
            f"failed payment, initiate the automatic reversal flow within "
            f"standard SLA."
        )

    if case_type == "refund_request":
        return (
            "Inform the customer that refund eligibility depends on the "
            "merchant's own policy. Provide guidance on contacting the merchant "
            "directly for a refund."
        )

    if case_type == "duplicate_payment":
        return (
            f"Verify the duplicate with payments_ops. If the biller confirms "
            f"only one payment was received, initiate reversal of {txn_id}."
        )

    if case_type == "merchant_settlement_delay":
        return (
            f"Route to merchant_operations to verify settlement batch status "
            f"for {txn_id}. If the batch is delayed, communicate a revised ETA "
            f"to the merchant."
        )

    if case_type == "agent_cash_in_issue":
        return (
            f"Investigate {txn_id} pending status with agent operations. "
            f"Confirm settlement state and resolve within the standard "
            f"cash-in SLA."
        )

    # Vague / other
    return (
        "Reply to customer asking for specific details: which transaction, "
        "what amount, what went wrong, and approximate time."
    )


# ---------- Component 9: Safe Customer Reply ----------
#
# SAFETY RULES (PDF page 8-9):
#   - NEVER ask for PIN, OTP, password, or full card number.
#   - NEVER confirm a refund, reversal, or account unblock without authority.
#     Use language like "any eligible amount will be returned through official
#     channels" instead of "we will refund you".
#   - NEVER instruct the customer to contact a third party outside official
#     channels.
#   - The user-facing customer_reply field MUST NOT name the specific
#     transaction_id (only the internal agent_summary / relevant_transaction_id
#     fields can mention it). This is the spec's separation rule: identifying
#     the case type is fine; leaking the specific txn ID to the customer is not.
#   - Language: respond in the customer's complaint language (en/bn).


def _lang_of(complaint: str, language: Optional[str]) -> str:
    """Return 'bn' if Bangla-script complaint, else 'en'."""
    if language in ("bn", "mixed"):
        # Heuristic: presence of Bangla chars → Bangla
        if any("\u0980" <= ch <= "\u09ff" for ch in complaint):
            return "bn"
        if language == "bn":
            return "bn"
    return "en"


def build_safe_customer_reply_stub(
    case_type: str,
    ticket_id: str,
    complaint: str,
    language: Optional[str] = None,
) -> str:
    """
    Build a safety-compliant customer-facing reply.
    NOTE: This function takes case_type and the complaint (for language
    detection) but NOT the transaction_id, so it cannot leak it.
    """
    lang = _lang_of(complaint, language)
    safety_tail_en = " Please do not share your PIN or OTP with anyone."
    safety_tail_bn = " অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"

    if case_type == "phishing_or_social_engineering":
        if lang == "bn":
            return (
                "আপনার সতর্কতার জন্য ধন্যবাদ। আমরা কখনোই আপনার পিন, ওটিপি বা "
                "পাসওয়ার্ড চাই না — কেউ আমাদের পক্ষ থেকে দাবি করলেও না। "
                "আমাদের ফ্রড টিম এই ঘটনা সম্পর্কে অবহিত হয়েছে।"
            )
        return (
            "Thank you for reaching out before sharing any information. We "
            "never ask for your PIN, OTP, or password under any circumstances. "
            "Please do not share these with anyone, even if they claim to be "
            "from us. Our fraud team has been notified of this incident."
        )

    if case_type == "wrong_transfer":
        if lang == "bn":
            return (
                "আমরা আপনার লেনদেন সংক্রান্ত অভিযোগটি পেয়েছি। "
                "আমাদের ডিসপিউট টিম এটি পর্যালোচনা করবে এবং অফিসিয়াল চ্যানেলে "
                "আপনার সাথে যোগাযোগ করবে।" + safety_tail_bn
            )
        return (
            "We have noted your concern about this transaction. "
            "Our dispute team will review the case and contact you through "
            "official support channels." + safety_tail_en
        )

    if case_type == "payment_failed":
        if lang == "bn":
            return (
                "আমরা আপনার পেমেন্ট সংক্রান্ত সমস্যাটি লক্ষ্য করেছি। "
                "আমাদের পেমেন্টস টিম যাচাই করবে এবং কোনো উপযুক্ত পরিমাণ "
                "অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে।" + safety_tail_bn
            )
        return (
            "We have noted that the payment may have caused an unexpected "
            "issue. Our payments team will review the case and any eligible "
            "amount will be returned through official channels." + safety_tail_en
        )

    if case_type == "refund_request":
        if lang == "bn":
            return (
                "আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। মার্চেন্ট পেমেন্টের "
                "রিফান্ড মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। মার্চেন্টের "
                "সাথে সরাসরি যোগাযোগ করার পরামর্শ দিচ্ছি।" + safety_tail_bn
            )
        return (
            "Thank you for reaching out. Refunds for completed merchant "
            "payments depend on the merchant's own policy. We recommend "
            "contacting the merchant directly. If you need help reaching them, "
            "please reply and we will guide you." + safety_tail_en
        )

    if case_type == "duplicate_payment":
        if lang == "bn":
            return (
                "সম্ভাব্য ডুপ্লিকেট পেমেন্ট সম্পর্কে আমরা অবগত হয়েছি। "
                "আমাদের পেমেন্টস টিম বিলারের সাথে যাচাই করবে এবং কোনো "
                "উপযুক্ত পরিমাণ অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে।" + safety_tail_bn
            )
        return (
            "We have noted the possible duplicate payment. Our payments team "
            "will verify with the biller and any eligible amount will be "
            "returned through official channels." + safety_tail_en
        )

    if case_type == "merchant_settlement_delay":
        if lang == "bn":
            return (
                "আমরা আপনার সেটেলমেন্ট সংক্রান্ত বিষয়টি লক্ষ্য করেছি। "
                "আমাদের মার্চেন্ট অপারেশন্স টিম ব্যাচের অবস্থা যাচাই করে "
                "অফিসিয়াল চ্যানেলে প্রত্যাশিত সেটেলমেন্ট সময় জানাবে।"
            )
        return (
            "We have noted your concern about the settlement. Our merchant "
            "operations team will check the batch status and update you on the "
            "expected settlement time through official channels."
        )

    if case_type == "agent_cash_in_issue":
        if lang == "bn":
            return (
                "আপনার লেনদেনের বিষয়ে আমরা অবগত হয়েছি। "
                "আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং "
                "অফিসিয়াল চ্যানেলে আপনাকে জানাবে।" + safety_tail_bn
            )
        return (
            "We have noted the issue with your cash-in transaction. "
            "Our agent operations team will verify it quickly and update you "
            "through official support channels." + safety_tail_en
        )

    # Vague / other
    if lang == "bn":
        return (
            "আপনার সাথে যোগাযোগ করার জন্য ধন্যবাদ। দ্রুত সাহায্য করতে, "
            "অনুগ্রহ করে লেনদেনের আইডি, পরিমাণ এবং সমস্যাটির সংক্ষিপ্ত বিবরণ "
            "জানান।" + safety_tail_bn
        )
    return (
        "Thank you for reaching out. To help you faster, please share the "
        "transaction ID, the amount involved, and a short description of what "
        "went wrong." + safety_tail_en
    )


# ---------- Final Assembler ----------


def analyze_ticket(ticket: TicketRequest) -> TicketResponse:
    """
    End-to-end analyzer. Wires Components 1-8 into a TicketResponse.
    Pure function: no I/O, no LLM calls, no global state mutation.
    """
    transactions = list(ticket.transaction_history or [])
    norm = normalize_text(ticket.complaint)

    # Component 1: matcher
    matched = find_relevant_transaction(ticket.complaint, transactions)

    # Duplicate-pair fallback: when two identical payments land within the
    # duplicate window, the matcher may return None (ambiguous top tier)
    # even though we KNOW which one is the suspected duplicate (the later).
    # Promote that to matched so relevant_transaction_id is populated.
    if matched is None and _has_any_hint(norm, DUPLICATE_HINTS):
        amounts = extract_numbers(ticket.complaint)
        intent_type = _detect_intent_type(norm)
        dup = _find_duplicate_pair(transactions, intent_type, amounts)
        if dup is not None:
            matched = dup

    # Component 2: verdict (returns tuple)
    verdict, verdict_reasons, confidence = determine_evidence_verdict(
        ticket.complaint, transactions, matched,
    )

    # Component 3: case type (uses verdict to gate refund_request)
    case_type = classify_case_type(
        ticket.complaint, transactions, matched,
        evidence_verdict=verdict,
    )

    # Component 4: department
    department = route_department(case_type, user_type=ticket.user_type)

    # Component 5: severity
    severity = classify_severity(
        case_type, verdict, matched, confidence, ticket.complaint,
    )

    # Component 6: human review (initial — uses verdict_reasons)
    human_review = requires_human_review(
        case_type=case_type,
        severity=severity,
        verdict=verdict,
        confidence=confidence,
        has_transactions=bool(transactions),
        user_type=ticket.user_type,
        reason_codes=verdict_reasons,
        matched_txn_present=matched is not None,
    )

    # Component 7: agent summary (internal — may include transaction_id)
    reason_codes = list(verdict_reasons)
    # Append semantic tags for human-readability
    if case_type == "wrong_transfer":
        reason_codes.append("wrong_transfer")
    elif case_type == "duplicate_payment":
        reason_codes.append("duplicate_payment")
    elif case_type == "payment_failed":
        reason_codes.append("payment_failed")
    elif case_type == "refund_request":
        reason_codes.append("refund_request")
    elif case_type == "merchant_settlement_delay":
        reason_codes.append("merchant_settlement")
    elif case_type == "agent_cash_in_issue":
        reason_codes.append("agent_cash_in")
    elif case_type == "phishing_or_social_engineering":
        reason_codes.append("phishing")

    if human_review and "human_review_required" not in reason_codes:
        reason_codes.append("human_review_required")

    agent_summary = build_agent_summary(
        ticket_id=ticket.ticket_id,
        matched=matched,
        case_type=case_type,
        verdict=verdict,
        reason_codes=reason_codes,
        complaint=ticket.complaint,
    )

    # Component 8: next action
    next_action = build_next_action(
        case_type=case_type,
        severity=severity,
        verdict=verdict,
        has_transactions=bool(transactions),
        matched=matched,
        complaint=ticket.complaint,
    )

    # Component 9: safe customer reply (NO transaction_id leaked)
    customer_reply = build_safe_customer_reply_stub(
        case_type=case_type,
        ticket_id=ticket.ticket_id,
        complaint=ticket.complaint,
        language=ticket.language,
    )

    # Clamp confidence to [0.0, 1.0]
    confidence = max(0.0, min(1.0, float(confidence)))

    return TicketResponse(
        ticket_id=ticket.ticket_id,
        relevant_transaction_id=(matched.transaction_id if matched else None),
        evidence_verdict=verdict,  # type: ignore[arg-type]
        case_type=case_type,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        department=department,  # type: ignore[arg-type]
        agent_summary=agent_summary,
        recommended_next_action=next_action,
        customer_reply=customer_reply,
        human_review_required=human_review,
        confidence=confidence,
        reason_codes=reason_codes,
    )
