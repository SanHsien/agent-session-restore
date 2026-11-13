<#
.SYNOPSIS
    Restore-AgentSessions.ps1 - Windows 11 Fast Multi-Agent Session Restorer

.DESCRIPTION
    Reads ~/.claude/claude-sessions.json and restores active sessions
    (Claude Code, Codex, Cursor, Antigravity, Hermes) in tabs under
    Windows Terminal or separate PowerShell windows.

.PARAMETER Terminal
    Terminal backend: 'wt' (Windows Terminal tabs), 'pwsh' (PowerShell 7), 'powershell' (Windows PS), 'cmd'.
    Defaults to 'wt' if Windows Terminal is installed.

.PARAMETER ActiveOnly
    Only restore sessions that were marked active (default: $true).

.PARAMETER Limit
    Maximum number of sessions to restore.

.PARAMETER DelayMs
    Delay in milliseconds between launching each session to prevent CPU/IO spikes (default: 300).

.PARAMETER DryRun
    Print what would be executed without launching.

.PARAMETER RegistryFile
    Custom path to claude-sessions.json.
#>

[CmdletBinding()]
param(
    [ValidateSet('wt', 'pwsh', 'powershell', 'cmd')]
    [string]$Terminal = $(if (Get-Command wt.exe -ErrorAction SilentlyContinue) { 'wt' } else { 'pwsh' }),

    [string]$Agent = "all",
    [switch]$All,
    [int]$Limit = 0,
    [int]$DelayMs = 300,
    [switch]$DryRun,
    [string]$RegistryFile = "$HOME/.claude/claude-sessions.json"
)

$ErrorActionPreference = 'Stop'

# --- Per-shell quoting helpers -----------------------------------------
# Dynamic values (session id, cwd, name, custom_resume_cmd) come from the
# registry JSON file and are treated as trusted-but-untrusted-shaped input:
# they are embedded into command STRINGS that PowerShell or cmd.exe will
# re-parse, so every dynamic value is quoted with the helper matching that
# shell's own escaping rules before being placed into such a string.

function ConvertTo-PSLiteral {
    # Wrap a value in a single-quoted PowerShell literal. Inside a
    # single-quoted string, PowerShell treats every character literally
    # except the single-quote characters, each escaped by doubling it. That is
    # not only the ASCII apostrophe: the tokenizer also accepts U+2018, U+2019,
    # U+201A and U+201B (regex escapes below keep this file ASCII).
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    return "'" + ($Value -replace "['\u2018\u2019\u201A\u201B]", '$0$0') + "'"
}

function ConvertTo-BatchToken {
    # Match quoting.quote_batch_token: one batch parse, percent signs doubled.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    if ($Value -eq '') { return '""' }
    if ($Value -match '^[\w.:/-]+\z') { return $Value }
    $sb = New-Object System.Text.StringBuilder
    $backslashes = 0
    $inQuote = $false
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq [char]34 -or $ch -eq '!') {
            if ($inQuote) {
                [void]$sb.Append([string]::new([char]92, $backslashes)).Append('"')
                $inQuote = $false
            }
            if ($ch -eq [char]34) { [void]$sb.Append('\^"') }
            else { [void]$sb.Append('^!') }
        } else {
            if (-not $inQuote) {
                [void]$sb.Append('"')
                $inQuote = $true
            }
            if ($ch -eq '%') { [void]$sb.Append('%%') }
            else { [void]$sb.Append($ch) }
        }
        if ($ch -eq [char]92) { $backslashes++ } else { $backslashes = 0 }
    }
    if ($inQuote) { [void]$sb.Append([string]::new([char]92, $backslashes)).Append('"') }
    return $sb.ToString()
}

function ConvertTo-BatchText {
    # Bare text for title, matching quoting.quote_batch_text.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '%') { [void]$sb.Append('%%') }
        else {
            if ('^&|<>()@!"'.IndexOf([string]$ch) -ge 0) { [void]$sb.Append('^') }
            [void]$sb.Append($ch)
        }
    }
    return $sb.ToString()
}

function ConvertTo-WinArg {
    # Quote one value as a single Windows command-line argument (the rules
    # CommandLineToArgvW / MSVCRT parse: wrap in double quotes when the value
    # has whitespace or quotes, backslash-escape embedded quotes and the
    # backslashes that precede them). Start-Process -ArgumentList does NOT do
    # this for array elements; it joins them with bare spaces, so a title or
    # directory containing a space would be split into several arguments.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    if ($Value -ne '' -and $Value -notmatch '[\s"]') { return $Value }
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('"')
    $backslashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq [char]92) {
            $backslashes++
        } elseif ($ch -eq [char]34) {
            [void]$sb.Append([string]::new([char]92, $backslashes * 2 + 1)).Append('"')
            $backslashes = 0
        } else {
            if ($backslashes -gt 0) { [void]$sb.Append([string]::new([char]92, $backslashes)) }
            $backslashes = 0
            [void]$sb.Append($ch)
        }
    }
    if ($backslashes -gt 0) { [void]$sb.Append([string]::new([char]92, $backslashes * 2)) }
    [void]$sb.Append('"')
    return $sb.ToString()
}

function Get-AgentResumeCommand {
    # Build the resume command as a string, quoting id/cwd/name for the
    # given shell dialect. The custom_resume_cmd template text itself
    # (authored by whoever registered the session) is left untouched --
    # it is intentionally an arbitrary, trusted command.
    param(
        [string]$AgentType,
        [string]$Id,
        [string]$Cwd,
        [string]$Name,
        [string]$CustomResumeCmd,
        [scriptblock]$Quote
    )
    if ($CustomResumeCmd) {
        $qid = & $Quote $Id
        $qcwd = & $Quote $Cwd
        $qname = & $Quote $Name
        return $CustomResumeCmd.Replace('{id}', $qid).Replace('{session_id}', $qid).Replace('{cwd}', $qcwd).Replace('{name}', $qname)
    }
    switch ($AgentType) {
        'codex'       { return "codex resume " + (& $Quote $Id) }
        'cursor'      { return "cursor " + (& $Quote $Cwd) }
        'antigravity' { return "agy resume " + (& $Quote $Id) }
        'hermes'      { return "hermes resume " + (& $Quote $Id) }
        default       { return "claude -r " + (& $Quote $Id) }
    }
}

# Session ids are internal identifiers; there is no legitimate reason for
# one to contain whitespace or shell metacharacters, so unlike name/cwd
# (which are only ever quoted, never rejected) a session whose id fails
# this pattern is skipped entirely rather than risking a quoting mistake.
$SafeIdPattern = '^[A-Za-z0-9_.:-]{1,128}\z'

# CR, LF and the other ASCII control characters (and DEL) end or corrupt a
# line of a batch file or command string, so a value carrying one could start
# a second command. Same set models.py strips from name and rejects in cwd.
$ControlCharPattern = '[\x00-\x1F\x7F]'

function Remove-ControlChars {
    param([AllowNull()][string]$Value)
    if ($null -eq $Value) { return '' }
    return $Value -replace $ControlCharPattern, ''
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Multi-Agent Fleet Session Restorer (Windows 11)" -ForegroundColor Cyan
Write-Host "    (Claude | Codex | Cursor | Antigravity | Hermes)" -ForegroundColor DarkGray
Write-Host "==================================================" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $RegistryFile)) {
    Write-Warning "Registry file not found at: $RegistryFile"
    Write-Host "No recorded sessions to restore. Start some sessions with the hooks installed first." -ForegroundColor Yellow
    exit 0
}

try {
    $rawContent = Get-Content -LiteralPath $RegistryFile -Raw -Encoding UTF8
    $registry = $rawContent | ConvertFrom-Json
} catch {
    Write-Error "Failed to parse registry JSON from ${RegistryFile}: $_"
    exit 1
}

$sessions = @()
if ($registry.sessions) {
    if ($registry.sessions -is [System.Management.Automation.PSCustomObject]) {
        foreach ($prop in $registry.sessions.PSObject.Properties) {
            $sessions += $prop.Value
        }
    } elseif ($registry.sessions -is [System.Array]) {
        $sessions = $registry.sessions
    }
}

if (-not $All) {
    $sessions = $sessions | Where-Object { $_.status -eq 'active' }
}

if ($Agent -and $Agent -ne 'all') {
    $sessions = $sessions | Where-Object { ($_.agent -and $_.agent.ToLower() -eq $Agent.ToLower()) -or (-not $_.agent -and $Agent -eq 'claude') }
}

# Sort by updated_at descending
$sessions = $sessions | Sort-Object -Property updated_at -Descending

if ($Limit -gt 0) {
    $sessions = $sessions | Select-Object -First $Limit
}

$totalCount = $sessions.Count
Write-Host "Found $totalCount session(s) to restore (Agent filter: $Agent | Backend: $Terminal)...`n" -ForegroundColor Green

if ($totalCount -eq 0) {
    Write-Host "No active sessions found matching criteria." -ForegroundColor DarkGray
    exit 0
}

$shellCmd = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }
$launched = 0
$failed = 0

foreach ($s in $sessions) {
    $id = $s.session_id
    $cwd = $s.cwd
    # Sanitise the free-text fields the same way models.py does. The agent
    # tag lands in the window title and must not carry control characters.
    $name = (Remove-ControlChars ([string]$s.name).Trim()).Trim()
    if (-not $name) { $name = (Remove-ControlChars ([System.IO.Path]::GetFileName([string]$cwd))).Trim() }
    $agentType = Remove-ControlChars ([string]$s.agent).Trim().ToLower()
    if (-not $agentType) { $agentType = "claude" }
    $agentTag = $agentType.ToUpper()
    $displayTitle = "[$agentTag] $name"

    Write-Host -NoNewline "[$($launched + 1)/$totalCount] " -ForegroundColor DarkGray
    Write-Host -NoNewline "[$agentTag] " -ForegroundColor Yellow
    Write-Host -NoNewline "$name " -ForegroundColor White
    Write-Host -NoNewline "($($id.Substring(0, [Math]::Min(8, $id.Length)))) " -ForegroundColor Magenta
    Write-Host "-> $cwd" -ForegroundColor DarkCyan

    if ($id -notmatch $SafeIdPattern) {
        Write-Warning "  Skipping session with unsafe id: $id"
        $failed++
        continue
    }

    if ([string]$cwd -match $ControlCharPattern) {
        Write-Warning "  Directory contains control characters (Skipping)"
        $failed++
        continue
    }

    if (-not (Test-Path -LiteralPath $cwd)) {
        Write-Warning "  Directory missing: $cwd (Skipping)"
        $failed++
        continue
    }

    # Derive the agent resume command, quoted for each shell dialect that
    # might actually run it. The custom_resume_cmd, id, cwd, and name all
    # come from the registry file, hence quoting rather than trusting them
    # to be free of shell metacharacters.
    $agentCmdPs = Get-AgentResumeCommand -AgentType $agentType -Id $id -Cwd $cwd -Name $name `
        -CustomResumeCmd $s.custom_resume_cmd -Quote ${function:ConvertTo-PSLiteral}
    $agentCmdCmd = Get-AgentResumeCommand -AgentType $agentType -Id $id -Cwd $cwd -Name $name `
        -CustomResumeCmd $s.custom_resume_cmd -Quote ${function:ConvertTo-BatchToken}

    if ($DryRun) {
        $plainCmd = Get-AgentResumeCommand -AgentType $agentType -Id $id -Cwd $cwd -Name $name `
            -CustomResumeCmd $s.custom_resume_cmd -Quote { param($v) $v }
        Write-Host "  [DRY-RUN] Would resume: $plainCmd in $cwd" -ForegroundColor Gray
        $launched++
        continue
    }

    $cmdScriptPath = $null
    try {
        if ($Terminal -eq 'wt') {
            # WT splits even quoted semicolons and removes one backslash
            # from each \;. Apply this before Windows argv quoting, also
            # to the inner PowerShell command and existing literal \; text.
            $wtArgs = @("-w", "0", "new-tab", "-d", $cwd, "--title", $displayTitle, $shellCmd, "-NoExit", "-Command", $agentCmdPs) |
                ForEach-Object { ConvertTo-WinArg ($_.Replace(';', '\;')) }
            Start-Process wt.exe -ArgumentList ($wtArgs -join ' ')
        } elseif ($Terminal -eq 'pwsh' -or $Terminal -eq 'powershell') {
            # Standalone PowerShell window.
            $titleAssignment = "`$host.UI.RawUI.WindowTitle = " + (ConvertTo-PSLiteral $displayTitle) + "; " + $agentCmdPs
            $psArgs = @("-NoExit", "-Command", $titleAssignment) | ForEach-Object { ConvertTo-WinArg $_ }
            Start-Process $shellCmd -WorkingDirectory $cwd -ArgumentList ($psArgs -join ' ')
        } elseif ($Terminal -eq 'cmd') {
            # Keep all session data in a UTF-8 batch file, never in /k text.
            # cmd reads the whole block before the first command deletes it.
            $lines = @(
                '@echo off'
                'chcp 65001 >nul'
                '('
                'del "%~f0" >nul 2>&1'
                ('title ' + (ConvertTo-BatchText $displayTitle))
                ('cd /d ' + (ConvertTo-BatchToken $cwd))
                $agentCmdCmd
                '(goto) 2>nul'
                ')'
            )
            $candidate = Join-Path ([System.IO.Path]::GetTempPath()) ("asr-" + [guid]::NewGuid().ToString('N') + '.cmd')
            $stream = [System.IO.File]::Open($candidate, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
            $cmdScriptPath = $candidate
            try {
                $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($lines -join "`r`n") + "`r`n")
                $stream.Write($bytes, 0, $bytes.Length)
            } finally {
                $stream.Dispose()
            }
            Start-Process cmd.exe -WorkingDirectory $cwd -ArgumentList ('/k call "' + $cmdScriptPath + '"')
        }

        $launched++
        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    } catch {
        if ($cmdScriptPath) {
            Remove-Item -LiteralPath $cmdScriptPath -Force -ErrorAction SilentlyContinue
        }
        Write-Error "  Failed to launch session: $_"
        $failed++
    }
}

Write-Host ""
Write-Host "Restore complete! Launched: $launched | Skipped/Failed: $failed" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
