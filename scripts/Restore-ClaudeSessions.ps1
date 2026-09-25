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

    [switch]$All,
    [int]$Limit = 0,
    [int]$DelayMs = 300,
    [switch]$DryRun,
    [string]$RegistryFile = "$HOME/.claude/claude-sessions.json"
)

$ErrorActionPreference = 'Stop'

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 🚀 Claude Code Fleet Session Restorer (Windows 11)" -ForegroundColor Cyan
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

# Sort by updated_at descending
$sessions = $sessions | Sort-Object -Property updated_at -Descending

if ($Limit -gt 0) {
    $sessions = $sessions | Select-Object -First $Limit
}

$totalCount = $sessions.Count
Write-Host "Found $totalCount session(s) to restore (Backend: $Terminal)...`n" -ForegroundColor Green

if ($totalCount -eq 0) {
    Write-Host "No active sessions found. Use -All to restore closed sessions." -ForegroundColor DarkGray
    exit 0
}

$shellCmd = if (Get-Command pwsh.exe -ErrorAction SilentlyContinue) { 'pwsh.exe' } else { 'powershell.exe' }
$launched = 0
$failed = 0

foreach ($s in $sessions) {
    $name = if ($s.name) { $s.name } else { [System.IO.Path]::GetFileName($s.cwd) }
    $id = $s.session_id
    $cwd = $s.cwd

    Write-Host -NoNewline "[$($launched + 1)/$totalCount] " -ForegroundColor DarkGray
    Write-Host -NoNewline "$name " -ForegroundColor White
    Write-Host -NoNewline "($($id.Substring(0, [Math]::Min(8, $id.Length)))) " -ForegroundColor Magenta
    Write-Host "-> $cwd" -ForegroundColor DarkCyan

    if (-not (Test-Path -LiteralPath $cwd)) {
        Write-Warning "  Directory missing: $cwd (Skipping)"
        $failed++
        continue
    }

    if ($DryRun) {
        Write-Host "  [DRY-RUN] Would resume: claude -r $id in $cwd" -ForegroundColor Gray
        $launched++
        continue
    }

    try {
        if ($Terminal -eq 'wt') {
            # Windows Terminal new tab
            Start-Process wt.exe -ArgumentList "-w", "0", "new-tab", "-d", "`"$cwd`"", "--title", "`"$name`"", $shellCmd, "-NoExit", "-Command", "claude -r $id"
        } elseif ($Terminal -eq 'pwsh' -or $Terminal -eq 'powershell') {
            # Standalone PowerShell window
            Start-Process $shellCmd -WorkingDirectory $cwd -ArgumentList "-NoExit", "-Command", "`$host.UI.RawUI.WindowTitle = 'Claude: $name'; claude -r $id"
        } elseif ($Terminal -eq 'cmd') {
            # Standalone Command Prompt
            Start-Process cmd.exe -WorkingDirectory $cwd -ArgumentList "/k", "title Claude: $name && claude -r $id"
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
