"""Tests for the per-shell quoting helpers."""

import pytest
from _cmdsim import parse_passes, win_argv

from agent_session_restore.quoting import (
    format_argv_batch,
    format_argv_cmd,
    format_argv_powershell,
    quote_batch_text,
    quote_batch_token,
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


def test_quote_cmd_token_does_not_leave_carets_in_a_value_with_a_percent_and_a_space():
    # A caret inside quotes is not removed, so `C:\My 100% dir` must not become
    # `"C:\My 100^% dir"`. The percent is moved outside the quotes instead,
    # where a caret does work and the variable-expansion marker is broken up.
    value = r"C:\My 100% dir"
    quoted = quote_cmd_token(value)
    assert quoted == '"C:\\My 100"^%" dir"'
    assert win_argv(parse_passes(quoted, 1)[0]) == [value]


def test_quote_cmd_token_breaks_up_a_percent_variable_reference_inside_quotes():
    quoted = quote_cmd_token("my %PATH% dir")
    assert quoted == '"my "^%"PATH"^%" dir"'
    assert win_argv(parse_passes(quoted, 1)[0]) == ["my %PATH% dir"]


NASTY_VALUES = [
    "plain",
    "R&D",
    "a|b<c>d",
    "x&calc",
    "50%",
    "%PATH%",
    "wow!",
    "C:\\work\\R&D (x86)",
    "has space",
    "has space & calc",
    "C:\\Program Files (x86)\\app",
    "my %PATH% dir",
    'say "hi" & calc',
    'x" & calc & "',
    "caret^ & space",
    'a"b&c',
    r"ends\with\backslash" + "\\",
    "C:\\dir with space\\",
    'quote after backslash \\" here',
    "",
]


@pytest.mark.parametrize("levels", [1, 2])
@pytest.mark.parametrize("value", NASTY_VALUES)
def test_quote_cmd_token_survives_every_cmd_parse_as_one_literal_argument(value, levels):
    # `levels` = how many times cmd.exe parses the line before the program
    # sees it. Command syntax must never be live in any parse, and the program
    # must end up with exactly the original value as a single argument.
    token = quote_cmd_token(value, levels)

    delivered, live = parse_passes(token, levels)

    assert live == [], f"live cmd syntax {live} in {token!r}"
    assert win_argv(delivered) == [value]


def test_quote_cmd_token_two_levels_escape_unquoted_metacharacters_twice():
    # `cmd.exe /c start ... cmd /k <line>`: the outer cmd consumes one layer of
    # carets, the inner cmd parses what is left.
    assert quote_cmd_token("R&D", levels=1) == "R^&D"
    assert quote_cmd_token("R&D", levels=2) == "R^^^&D"


def test_quote_batch_token_leaves_plain_tokens_alone():
    assert format_argv_batch(["claude", "-r", "abc-123", "C:/proj"]) == "claude -r abc-123 C:/proj"


def test_quote_batch_token_doubles_percent_because_batch_expands_it_first():
    # A caret cannot stop %VAR% expansion in a script file; only %% does.
    assert quote_batch_token("%PATH%") == '"%%PATH%%"'


def test_quote_batch_token_keeps_one_argument_with_no_live_syntax():
    value = 'a & b | c ^ d ! "q" ' + chr(92) + " (e)"
    delivered, live = parse_passes(quote_batch_token(value), 1)
    assert live == []
    assert win_argv(delivered.replace("%%", "%")) == [value]


def test_quote_batch_token_empty_value_is_one_empty_argument():
    assert win_argv(quote_batch_token("")) == [""]


def test_quote_batch_text_escapes_metacharacters_and_percent():
    text = quote_batch_text('x" & calc | 100%')
    assert text == 'x^" ^& calc ^| 100%%'
    delivered, live = parse_passes(text, 1)
    assert live == []
    assert delivered == 'x" & calc | 100%%'
