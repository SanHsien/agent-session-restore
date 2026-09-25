<#
.SYNOPSIS
    Restore-ClaudeSessions.ps1 — Windows 11 Fast Claude Code Session Restorer

.DESCRIPTION
    Reads ~/.claude/claude-sessions.json and restores active sessions
    in tabs under Windows Terminal or separate PowerShell windows.

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

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 🚀 Multi-Agent Fleet Session Restorer (Windows 11)" -ForegroundColor Cyan
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
    Write-Error "Failed to parse registry JSON from $RegistryFile: $_"
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

    # Derive agent CLI command
    $agentCmd = "claude -r $id"
    if ($s.custom_resume_cmd) {
        $agentCmd = $s.custom_resume_cmd.Replace('{id}', $id).Replace('{session_id}', $id).Replace('{cwd}', $cwd).Replace('{name}', $name)
    } elseif ($agentType -eq 'codex') {
        $agentCmd = "codex resume $id"
    } elseif ($agentType -eq 'cursor') {
        $agentCmd = "cursor `"$cwd`""
    } elseif ($agentType -eq 'antigravity') {
        $agentCmd = "agy resume $id"
    } elseif ($agentType -eq 'hermes') {
        $agentCmd = "hermes resume $id"
    }

    Write-Host -NoNewline "[$($launched + 1)/$totalCount] " -ForegroundColor DarkGray
    Write-Host -NoNewline "[$agentTag] " -ForegroundColor Yellow
    Write-Host -NoNewline "$name " -ForegroundColor White
    Write-Host -NoNewline "($($id.Substring(0, [Math]::Min(8, $id.Length)))) " -ForegroundColor Magenta
    Write-Host "-> $cwd" -ForegroundColor DarkCyan

    if (-not (Test-Path -LiteralPath $cwd)) {
        Write-Warning "  Directory missing: $cwd (Skipping)"
        $failed++
        continue
    }

    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would resume: $agentCmd in $cwd" -ForegroundColor Gray
        $launched++
        continue
    }

    try {
        if ($Terminal -eq 'wt') {
            # Windows Terminal new tab
            Start-Process wt.exe -ArgumentList "-w", "0", "new-tab", "-d", "`"$cwd`"", "--title", "`"$displayTitle`"", $shellCmd, "-NoExit", "-Command", $agentCmd
        } elseif ($Terminal -eq 'pwsh' -or $Terminal -eq 'powershell') {
            # Standalone PowerShell window
            Start-Process $shellCmd -WorkingDirectory $cwd -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle = '$displayTitle'; $agentCmd"
        } elseif ($Terminal -eq 'cmd') {
            # Standalone Command Prompt
            Start-Process cmd.exe -WorkingDirectory $cwd -ArgumentList "/k", "title $displayTitle && $agentCmd"
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
