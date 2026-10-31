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

import re

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


def _caret_escape(text: str, levels: int) -> str:
    """Caret-escape cmd metacharacters once per cmd.exe parse the text will go through."""
    for _ in range(levels):
        text = "".join(("^" + ch) if ch in _CMD_METACHARS else ch for ch in text)
    return text


def quote_cmd_token(value: str, levels: int = 1) -> str:
    """Quote ``value`` as ONE argument inside a cmd.exe command-line string.

    ``levels`` is how many times cmd.exe parses the text before the program
    sees it. A plain ``cmd /c <line>`` is one parse. ``cmd /c start ... cmd /k
    <line>`` is two: the outer cmd.exe parses the whole line (consuming one
    layer of carets), then the cmd.exe that ``start`` launches parses the /k
    text again, so an unquoted metacharacter must be escaped once per parse
    (``&`` -> ``^^^&`` for two parses).

    * No whitespace: every metacharacter is caret-escaped (once per level).
    * Whitespace: the value is wrapped in double quotes, where cmd.exe treats
      ``& | < > ( ) @ ^`` literally in every parse. A caret inside quotes is
      NOT removed, so none are added there. The characters that still act
      inside quotes (``%`` and ``!`` expansion) and the literal double quote
      are emitted outside the quotes, where a caret does work: the quoted
      region is closed, the character is caret-escaped and the region is
      reopened (``a %X% b`` -> ``"a "^%"X"^%" b``; the program still gets one
      argument).
    * A literal double quote is passed on as ``\"`` and backslashes in front of
      a quote are doubled, as the program's own command-line parser expects.
    """
    if not value:
        return '""'

    backslashes = 0
    out: list[str] = []

    if not any(c.isspace() for c in value):
        for ch in value:
            if ch == '"':
                out.append(_caret_escape("\\" * (backslashes + 1) + '"', levels))
            else:
                out.append(_caret_escape(ch, levels))
            backslashes = backslashes + 1 if ch == "\\" else 0
        return "".join(out)

    in_quote = False
    for ch in value:
        if ch in '"%!':
            if in_quote:
                out.append("\\" * backslashes + '"')
                in_quote = False
            out.append(_caret_escape('\\"' if ch == '"' else ch, levels))
        else:
            if not in_quote:
                out.append('"')
                in_quote = True
            out.append(ch)
        backslashes = backslashes + 1 if ch == "\\" else 0
    if in_quote:
        out.append("\\" * backslashes + '"')
    return "".join(out)


def format_argv_powershell(argv: list[str]) -> str:
    """Join an argv list into a PowerShell command string, quoting every token."""
    return " ".join(quote_powershell(token) for token in argv)


def format_argv_cmd(argv: list[str], levels: int = 1) -> str:
    """Join an argv list into a cmd.exe command string, escaping every token.

    ``levels`` is the number of cmd.exe parses the string goes through (see
    :func:`quote_cmd_token`).
    """
    return " ".join(quote_cmd_token(token, levels) for token in argv)


_BATCH_PLAIN = re.compile(r"[\w.:\/-]+")


def quote_batch_token(value: str) -> str:
    r"""Quote ``value`` as ONE argument on a line of a ``.cmd`` script file.

    A script line is parsed by cmd.exe exactly once (there is no outer ``/c``
    line), but batch files expand ``%`` before anything else, so a literal
    percent sign must be doubled (``%%``); a caret cannot protect it. The rest
    follows :func:`quote_cmd_token` with one level: the value is wrapped in
    double quotes (where ``& | < > ( ) @ ^`` are literal) and a literal ``"``
    or ``!`` is emitted outside the quotes behind a caret. A value made only
    of word characters and ``. : / -`` is returned unchanged.
    """
    if not value:
        return '""'
    if _BATCH_PLAIN.fullmatch(value):
        return value

    backslashes = 0
    in_quote = False
    out: list[str] = []
    for ch in value:
        if ch in '"!':
            if in_quote:
                out.append("\\" * backslashes + '"')
                in_quote = False
            out.append('\\^"' if ch == '"' else "^!")
        else:
            if not in_quote:
                out.append('"')
                in_quote = True
            out.append("%%" if ch == "%" else ch)
        backslashes = backslashes + 1 if ch == "\\" else 0
    if in_quote:
        out.append("\\" * backslashes + '"')
    return "".join(out)


def quote_batch_text(value: str) -> str:
    """Escape ``value`` as bare (unquoted) text on a ``.cmd`` script line.

    For commands such as ``title`` that take the rest of the line literally:
    ``%`` is doubled and every other cmd metacharacter is caret-escaped.
    """
    return "".join(
        "%%" if ch == "%" else ("^" + ch if ch in _CMD_METACHARS else ch) for ch in value
    )


def format_argv_batch(argv: list[str]) -> str:
    """Join an argv list into one ``.cmd`` script line, quoting every token."""
    return " ".join(quote_batch_token(token) for token in argv)
