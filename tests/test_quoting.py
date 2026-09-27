"""Tests for the per-shell quoting helpers."""

from agent_session_restore.quoting import (
    format_argv_cmd,
    format_argv_powershell,
    quote_cmd_token,
    quote_powershell,
)


def test_quote_powershell_wraps_plain_value():
    assert quote_powershell("codex") == "'codex'"


def test_quote_powershell_escapes_embedded_single_quote():
    assert quote_powershell("it's here") == "'it''s here'"


def test_quote_powershell_is_inert_for_other_metacharacters():
    # Inside a PowerShell single-quoted literal, none of these are special.
    value = 'x" & calc & "; $(rm -rf /) `whoami`'
    quoted = quote_powershell(value)
    assert quoted == "'" + value + "'"
    assert quoted.startswith("'") and quoted.endswith("'")
    assert quoted.count("'") == 2


def test_quote_cmd_token_leaves_plain_token_alone():
    assert quote_cmd_token("codex") == "codex"


def test_quote_cmd_token_escapes_metacharacters():
    quoted = quote_cmd_token("a&b|c^d%e")
    assert "^&" in quoted
    assert "^|" in quoted
    assert "^^" in quoted
    assert "^%" in quoted
    assert "&" not in quoted.replace("^&", "")


def test_quote_cmd_token_wraps_in_quotes_when_it_contains_whitespace():
    quoted = quote_cmd_token("has space")
    assert quoted.startswith('"') and quoted.endswith('"')


def test_quote_cmd_token_neutralizes_percent_expansion_marker():
    quoted = quote_cmd_token("%PATH%")
    assert "^%" in quoted
    assert quoted.count("%") == 2  # both percents are still present, just escaped


def test_format_argv_powershell_quotes_every_token():
    result = format_argv_powershell(["codex", "resume", "abc-123"])
    assert result == "'codex' 'resume' 'abc-123'"


def test_format_argv_cmd_only_escapes_tokens_that_need_it():
    result = format_argv_cmd(["claude", "-r", "abc-123"])
    assert result == "claude -r abc-123"


def test_format_argv_cmd_escapes_dangerous_token():
    result = format_argv_cmd(["cursor", "C:/proj & calc"])
    assert "^&" in result
    assert result.count('"') >= 2
