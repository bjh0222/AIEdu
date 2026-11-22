"""A complete A response with invalid JSON may receive one explicit correction."""
from app.v2.contracts import DomainError
from app.v2.worker import understanding_json_correction_candidate


def test_only_complete_invalid_json_is_a_correction_candidate():
    eligible = DomainError(
        "PROVIDER_CALL_FAILED",
        502,
        call_id="original-call",
        failure_code="PROVIDER_OUTPUT_INVALID_OR_TRUNCATED",
        failure_diagnostics={
            "finish_reason": "stop",
            "validation_reason": "JSON_PARSE_FAILED",
            "content_characters": 8893,
        },
    )
    assert understanding_json_correction_candidate(eligible) == {
        "call_id": "original-call",
        "failure_code": "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED",
        "failure_diagnostics": {
            "finish_reason": "stop",
            "validation_reason": "JSON_PARSE_FAILED",
            "content_characters": 8893,
        },
    }

    for diagnostics in (
        {"finish_reason": "length", "validation_reason": "OUTPUT_LENGTH_LIMIT"},
        {"finish_reason": "stop", "validation_reason": "EMPTY_OR_NON_STRING_CONTENT"},
    ):
        assert understanding_json_correction_candidate(
            DomainError(
                "PROVIDER_CALL_FAILED",
                502,
                call_id="original-call",
                failure_code="PROVIDER_OUTPUT_INVALID_OR_TRUNCATED",
                failure_diagnostics=diagnostics,
            )
        ) is None

    assert understanding_json_correction_candidate(
        DomainError("UNKNOWN_CALL", 502)
    ) is None
    assert understanding_json_correction_candidate(
        DomainError(
            "PROVIDER_CALL_FAILED",
            502,
            call_id="original-call",
            failure_code="PROVIDER_HTTP_502",
            failure_diagnostics={"finish_reason": "stop"},
        )
    ) is None
