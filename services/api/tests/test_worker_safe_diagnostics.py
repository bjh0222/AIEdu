def test_worker_error_diagnostics_never_save_message_or_source_line():
    from app.v2.worker import safe_worker_diagnostics
    secret = "fixture-only-private-token"
    try:
        raise ValueError(secret)
    except ValueError as exc:
        result = safe_worker_diagnostics(exc)
    assert secret not in str(result)
    assert result["exception_type"] == "ValueError"
    assert result["exception_text_saved"] is False
    assert all(set(f) == {"file", "line"} for f in result["frames"])
