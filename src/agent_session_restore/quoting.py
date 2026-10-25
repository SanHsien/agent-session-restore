"""Per-shell quoting helpers for safely embedding dynamic values (session id,
name, cwd) into command-line strings for PowerShell and cmd.exe.

These are used wherever a value has to be interpolated into a script/command
STRING that a shell will re-parse (e.g. the text passed to ``pwsh -Command``
or the text that ``cmd.exe /c`` re-tokenizes after ``/c``). Plain argv-list
subprocess calls (``shell=False``) do not need these helpers, because the
target executable receives each argument as a discrete string with no
shell re-parsing involved.
"""

from __future__ import annotations

# Characters cmd.exe treats specially, even inside a quoted argument, because
# cmd.exe re-parses the whole command line itself after `/c`. Preceding one of
# these with `^` neutralizes it. This list follows the commonly documented
# cmd.exe metacharacter set: & | < > ^ ( ) @ ! " and %  (percent for variable
# expansion, including delayed expansion with !).
_CMD_METACHARS = set('^&|<>()@!%"')


def quote_powershell(value: str) -> str:
    """Wrap ``value`` in a single-quoted PowerShell literal.

    PowerShell single-quoted strings are fully literal: `$`, backtick,
    `;`, `"`, `&`, `|`, `%%` etc. all lose any special meaning inside them.
    The only character that needs escaping is the single quote itself,
    which PowerShell doubles (`''`) to represent a literal `'`.
    """
    return "'" + value.replace("'", "''") + "'"


def quote_cmd_token(value: str) -> str:
    """Escape ``value`` for safe embedding in a cmd.exe command-line string.

    cmd.exe re-parses its entire command line (including text following
    ``/c``) with its own metacharacter rules, independent of how the host
    process quoted argv boundaries. Each metacharacter is escaped with a
    caret so cmd.exe treats it as a literal character rather than as a
    control operator or variable-expansion marker.
    """
    needs_quotes = not value or any(c.isspace() for c in value)

    if needs_quotes and '"' not in value:
        # Inside a quoted string cmd.exe treats & | < > ( ) @ ^ literally, and
        # a caret there is NOT consumed -- it would end up in the value (a path
        # like "Program Files (x86)" would become "...^(x86^)" and not exist).
        # Only variable expansion (% and delayed !) still happens in quotes, so
        # only those are caret-escaped. This is safe only because there is no
        # embedded quote that could end the quoted region early.
        escaped = "".join(("^" + ch) if ch in "%!" else ch for ch in value)
        return f'"{escaped}"'

    escaped = "".join(("^" + ch) if ch in _CMD_METACHARS else ch for ch in value)
    if needs_quotes:
        escaped = f'"{escaped}"'
    return escaped


def format_argv_powershell(argv: list[str]) -> str:
    """Join an argv list into a PowerShell command string, quoting every token."""
    return " ".join(quote_powershell(token) for token in argv)


def format_argv_cmd(argv: list[str]) -> str:
    """Join an argv list into a cmd.exe command string, escaping every token."""
    return " ".join(quote_cmd_token(token) for token in argv)
