import json
import os
import sys
import httpx
import pytest

# Path configurations
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JSON_PATH = os.path.join(BASE_DIR, "SUST_Preli_Sample_Cases.json")
API_URL = "http://localhost:8000/analyze-ticket"

def load_sample_cases():
    if not os.path.exists(JSON_PATH):
        raise FileNotFoundError(f"Sample cases JSON file not found at {JSON_PATH}")
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("cases", [])

# Valid enums for assertion checks
ALLOWED_EVIDENCE_VERDICTS = {"consistent", "inconsistent", "insufficient_data"}
ALLOWED_CASE_TYPES = {
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "duplicate_payment",
    "merchant_settlement_delay",
    "agent_cash_in_issue",
    "phishing_or_social_engineering",
    "other",
}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}
ALLOWED_DEPARTMENTS = {
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
}

def validate_response(input_data, response_data):
    # Verify ticket_id in response matches ticket_id in input
    assert response_data.get("ticket_id") == input_data.get("ticket_id"), (
        f"ticket_id mismatch: expected {input_data.get('ticket_id')}, got {response_data.get('ticket_id')}"
    )
    
    # Required output fields validation
    required_fields = [
        "ticket_id",
        "relevant_transaction_id",
        "evidence_verdict",
        "case_type",
        "severity",
        "department",
        "agent_summary",
        "recommended_next_action",
        "customer_reply",
        "human_review_required",
    ]
    for field in required_fields:
        assert field in response_data, f"Missing required output field: {field}"
        
    # Verify valid enum values
    assert response_data["evidence_verdict"] in ALLOWED_EVIDENCE_VERDICTS, (
        f"Invalid evidence_verdict: {response_data['evidence_verdict']}"
    )
    assert response_data["case_type"] in ALLOWED_CASE_TYPES, (
        f"Invalid case_type: {response_data['case_type']}"
    )
    assert response_data["severity"] in ALLOWED_SEVERITIES, (
        f"Invalid severity: {response_data['severity']}"
    )
    assert response_data["department"] in ALLOWED_DEPARTMENTS, (
        f"Invalid department: {response_data['department']}"
    )
    
    # customer_reply safety check: does not ask for PIN, OTP, password
    customer_reply_lower = response_data["customer_reply"].lower()
    
    # Check if the reply requests secrets (ignoring warnings like "do not share your PIN/OTP")
    credential_request_phrases = [
        "please enter your pin", "tell us your pin", "send your pin", "provide your pin",
        "please enter your otp", "tell us your otp", "send your otp", "provide your otp",
        "please enter your password", "tell us your password", "send your password", "provide your password"
    ]
    for phrase in credential_request_phrases:
        assert phrase not in customer_reply_lower, f"Safety violation: customer_reply requests credentials with phrase '{phrase}'"

    # Special check for "share your pin/otp/password" to make sure it's not a request
    for secret in ["pin", "otp", "password"]:
        share_phrase = f"share your {secret}"
        if share_phrase in customer_reply_lower:
            idx = customer_reply_lower.find(share_phrase)
            context = customer_reply_lower[max(0, idx - 15):idx]
            is_warning = any(w in context for w in ["do not", "don't", "never", "should not", "must not"])
            assert is_warning, f"Safety violation: customer_reply requests credentials by asking to '{share_phrase}' without warning context"

@pytest.mark.parametrize("case", load_sample_cases())
def test_sample_case(case):
    input_data = case["input"]
    with httpx.Client(timeout=30.0) as client:
        response = client.post(API_URL, json=input_data)
        assert response.status_code == 200, f"Case {case['id']} failed with status {response.status_code}: {response.text}"
        response_data = response.json()
        validate_response(input_data, response_data)

def main():
    print("=" * 60)
    print("QueueStorm Investigator - Sample Cases Test Runner")
    print("=" * 60)
    
    try:
        cases = load_sample_cases()
    except Exception as e:
        print(f"Error loading sample cases: {e}")
        sys.exit(1)
        
    passed_count = 0
    failed_count = 0
    
    with httpx.Client(timeout=30.0) as client:
        for case in cases:
            case_id = case["id"]
            label = case["label"]
            input_data = case["input"]
            
            print(f"Running {case_id}: {label} ... ", end="")
            
            try:
                response = client.post(API_URL, json=input_data)
                if response.status_code != 200:
                    print(f"FAIL (HTTP {response.status_code})")
                    print(f"  Response: {response.text}")
                    failed_count += 1
                    continue
                
                response_data = response.json()
                validate_response(input_data, response_data)
                print("PASS")
                passed_count += 1
            except Exception as e:
                print(f"FAIL (Error: {e})")
                failed_count += 1
                
    print("=" * 60)
    print(f"Summary: {passed_count} passed, {failed_count} failed out of {len(cases)}")
    print("=" * 60)
    
    if failed_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
