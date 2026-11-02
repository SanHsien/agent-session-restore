"""Parse PowerShell text with the real parser (never executes it).

Used to assert that quoted session values stay inside a string literal: a
quote character the quoting function missed shows up as an extra command.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_PARSE = r"""
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $env:ASR_PS_TEXT, [ref]$tokens, [ref]$errors)
$commands = @($ast.FindAll(
    { param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true
) | ForEach-Object { $_.GetCommandName() })
$result = [ordered]@{ commands = $commands; errors = @($errors | ForEach-Object { $_.Message }) }
$json = ConvertTo-Json -InputObject $result -Compress
# Keep stdout pure ASCII: error messages are localised and the console code page varies.
[Console]::Out.Write([regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value }))
"""

# All characters PowerShell treats as a single quote.
SINGLE_QUOTES = ["'", "\u2018", "\u2019", "\u201a", "\u201b"]
UNICODE_SINGLE_QUOTES = SINGLE_QUOTES[1:]


def have_pwsh() -> bool:
    return shutil.which("pwsh") is not None


def parse(text: str) -> tuple[list[str | None], list[str]]:
    """Return (command names, parse error messages) for ``text``."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", _PARSE],
        capture_output=True, timeout=120, creationflags=flags,
        env={**os.environ, "ASR_PS_TEXT": text, "PYTHONUTF8": "1"},
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    result = json.loads(proc.stdout.decode("utf-8-sig"))
    commands, errors = result["commands"], result["errors"]
    # ConvertTo-Json collapses a one-element array into a scalar in some versions.
    return (
        commands if isinstance(commands, list) else [commands],
        errors if isinstance(errors, list) else [errors],
    )


def command_names(text: str) -> list[str | None]:
    return parse(text)[0]
