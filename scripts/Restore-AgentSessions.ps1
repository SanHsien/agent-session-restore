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
    # except the quote character itself, which is escaped by doubling it.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function ConvertTo-CmdSafe {
    # Escape a value for safe embedding in a cmd.exe command-line string.
    # cmd.exe re-parses its entire command line (including text following
    # /c) with its own metacharacter rules, so each metacharacter is
    # caret-escaped rather than relying on argv-boundary quoting alone.
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    $metaChars = '^&|<>()@!%"'
    $sb = New-Object System.Text.StringBuilder
    foreach ($ch in $Value.ToCharArray()) {
        if ($metaChars.IndexOf([string]$ch) -ge 0) {
            [void]$sb.Append('^')
        }
        [void]$sb.Append($ch)
    }
    $result = $sb.ToString()
    if ($result -eq '' -or $result -match '\s') {
        $result = '"' + $result + '"'
    }
    return $result
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
$SafeIdPattern = '^[A-Za-z0-9_.:-]{1,128}$'

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
    $name = if ($s.name) { $s.name } else { [System.IO.Path]::GetFileName($s.cwd) }
    $id = $s.session_id
    $cwd = $s.cwd
    $agentType = if ($s.agent) { $s.agent.ToLower() } else { "claude" }
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
        -CustomResumeCmd $s.custom_resume_cmd -Quote ${function:ConvertTo-CmdSafe}

    if ($DryRun) {
        $plainCmd = Get-AgentResumeCommand -AgentType $agentType -Id $id -Cwd $cwd -Name $name `
            -CustomResumeCmd $s.custom_resume_cmd -Quote { param($v) $v }
        Write-Host "  [DRY-RUN] Would resume: $plainCmd in $cwd" -ForegroundColor Gray
        $launched++
        continue
    }

    try {
        if ($Terminal -eq 'wt') {
            # Windows Terminal new tab. -d/--title are separate argv elements
            # to wt.exe itself (no shell re-parsing); $agentCmdPs is the text
            # the started pwsh -Command will run, already PS-literal quoted.
            Start-Process wt.exe -ArgumentList "-w", "0", "new-tab", "-d", $cwd, "--title", $displayTitle, $shellCmd, "-NoExit", "-Command", $agentCmdPs
        } elseif ($Terminal -eq 'pwsh' -or $Terminal -eq 'powershell') {
            # Standalone PowerShell window.
            $titleAssignment = "`$host.UI.RawUI.WindowTitle = " + (ConvertTo-PSLiteral $displayTitle) + "; " + $agentCmdPs
            Start-Process $shellCmd -WorkingDirectory $cwd -ArgumentList "-NoExit", "-Command", $titleAssignment
        } elseif ($Terminal -eq 'cmd') {
            # Standalone Command Prompt. cmd.exe re-parses everything after
            # /k with its own metacharacter rules, so title and the resume
            # command both use the cmd-specific escaping helper here.
            $cmdLine = "title " + (ConvertTo-CmdSafe $displayTitle) + " && " + $agentCmdCmd
            Start-Process cmd.exe -WorkingDirectory $cwd -ArgumentList "/k", $cmdLine
        }

        $launched++
        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    } catch {
        Write-Error "  Failed to launch session: $_"
        $failed++
    }
}

Write-Host ""
Write-Host "Restore complete! Launched: $launched | Skipped/Failed: $failed" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
