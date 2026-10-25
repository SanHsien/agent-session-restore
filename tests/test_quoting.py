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


def test_format_argv_cmd_keeps_metacharacters_inert_inside_quotes():
    # The whole token is in quotes, where & is literal to cmd.exe. A caret in
    # there would not be removed by cmd.exe: it would reach the program.
    result = format_argv_cmd(["cursor", "C:/proj & calc"])
    assert result == 'cursor "C:/proj & calc"'


def test_quote_cmd_token_does_not_corrupt_a_path_with_spaces_and_parentheses():
    # "Program Files (x86)" style paths: carets inside the quotes are literal,
    # so the old output named a directory that does not exist.
    path = r"C:\Program Files (x86)\my app"
    assert quote_cmd_token(path) == '"' + path + '"'


def test_quote_cmd_token_still_escapes_percent_inside_quotes():
    quoted = quote_cmd_token("my %PATH% dir")
    assert quoted.startswith('"') and quoted.endswith('"')
    assert "^%" in quoted


def test_quote_cmd_token_with_quote_and_whitespace_escapes_everything():
    # A literal double quote flips cmd.exe's quote state, so every following
    # metacharacter must stay caret-escaped (nothing may rely on being quoted).
    quoted = quote_cmd_token('say "hi" & calc')
    assert "^&" in quoted
    assert '^"' in quoted
