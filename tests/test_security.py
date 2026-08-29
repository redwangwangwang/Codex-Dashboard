from codex_control_center.models import HostInput
from codex_control_center.security import redact, safe_tag_list, ssh_base_argv


def test_redaction_preserves_numeric_usage():
    value = redact({"input_tokens": 120, "access_token": "secret-value", "nested": {"api_key": "abc"}})
    assert value["input_tokens"] == 120
    assert value["access_token"] == "[REDACTED]"
    assert value["nested"]["api_key"] == "[REDACTED]"


def test_host_rejects_password():
    try:
        HostInput(name="x", hostname="example.com", username="u", password="nope")
    except ValueError as exc:
        assert "password" in str(exc)
    else:
        raise AssertionError("password was accepted")


def test_ssh_is_batch_and_strict_by_default():
    host = HostInput(name="x", hostname="example.com", username="u")
    argv = ssh_base_argv(host)
    joined = " ".join(argv)
    assert "BatchMode=yes" in joined
    assert "StrictHostKeyChecking=yes" in joined
    assert argv[-1] == "u@example.com"


def test_tags_are_bounded_and_unique():
    assert safe_tag_list([" a ", "a", "b"], max_items=2) == ["a", "b"]
