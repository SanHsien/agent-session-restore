"""A small model of how cmd.exe and a child program read a command line.

Only what the quoting tests need, and pure string work: nothing here starts a
process. One cmd.exe parse (outside quotes a caret escapes the next character
and is removed; inside quotes a caret is literal; & | < > are command syntax
outside quotes; an escaped quote does not toggle the quote state), followed by
the child's own CommandLineToArgvW split.
"""

from __future__ import annotations

CMD_SYNTAX = set("&|<>")


def cmd_parse(line: str) -> tuple[str, list[str]]:
    """Return (text cmd.exe hands on, unescaped command-syntax chars outside quotes)."""
    out: list[str] = []
    live_syntax: list[str] = []
    in_quote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if not in_quote and ch == "^" and i + 1 < len(line):
            out.append(line[i + 1])
            i += 2
            continue
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch in CMD_SYNTAX:
            live_syntax.append(ch)
        out.append(ch)
        i += 1
    return "".join(out), live_syntax


def parse_passes(line: str, passes: int) -> tuple[str, list[str]]:
    """Run `passes` cmd.exe parses; collect every live command-syntax char seen."""
    live: list[str] = []
    for _ in range(passes):
        line, found = cmd_parse(line)
        live.extend(found)
    return line, live


def win_argv(line: str) -> list[str]:
    """CommandLineToArgvW-style split (quotes group, backslash-quote is a literal quote)."""
    args: list[str] = []
    cur: list[str] = []
    started = False
    in_quote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            j = i
            while j < len(line) and line[j] == "\\":
                j += 1
            n = j - i
            started = True
            if j < len(line) and line[j] == '"':
                cur.append("\\" * (n // 2))
                if n % 2:
                    cur.append('"')
                    i = j + 1
                else:
                    i = j
                continue
            cur.append("\\" * n)
            i = j
            continue
        if ch == '"':
            in_quote = not in_quote
            started = True
        elif ch.isspace() and not in_quote:
            if started:
                args.append("".join(cur))
                cur, started = [], False
        else:
            cur.append(ch)
            started = True
        i += 1
    if started:
        args.append("".join(cur))
    return args
