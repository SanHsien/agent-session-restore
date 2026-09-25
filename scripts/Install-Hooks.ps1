<#
.SYNOPSIS
    Install-Hooks.ps1 — Helper to register SessionStart/End hooks into ~/.claude/settings.json
#>

[CmdletBinding()]
param(
    [string]$SettingsPath = "$HOME/.claude/settings.json",
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path "$PSScriptRoot/..").Path
$startHook = Join-Path $repoRoot "hooks\session-start.py"
$endHook = Join-Path $repoRoot "hooks\session-end.py"

Write-Host "Configuring Claude Code hooks..." -ForegroundColor Cyan
Write-Host "Target settings: $SettingsPath"

$claudeDir = [System.IO.Path]::GetDirectoryName($SettingsPath)
if (-not (Test-Path $claudeDir)) {
    New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null
}

$settings = [ordered]@{}
if (Test-Path $SettingsPath) {
    # Create backup before modifying
    $backupPath = "$SettingsPath.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $SettingsPath -Destination $backupPath -Force
    Write-Host "Created settings backup: $backupPath" -ForegroundColor DarkGray

    try {
        $content = Get-Content -LiteralPath $SettingsPath -Raw -Encoding UTF8
        if ($content.Trim()) {
            $settings = $content | ConvertFrom-Json -AsHashtable
        }
    } catch {
        Write-Warning "Could not parse existing settings.json. Starting fresh."
    }
}

if (-not $settings.ContainsKey("hooks")) {
    $settings["hooks"] = [ordered]@{}
}

$startHookCmd = "python `"$startHook`""
$endHookCmd = "python `"$endHook`""

# Register SessionStart hook
$startHooksList = @()
if ($settings["hooks"].ContainsKey("SessionStart")) {
    $existing = @($settings["hooks"]["SessionStart"])
    # Filter out previous instances of this hook
    $startHooksList = $existing | Where-Object { $_.command -notlike "*session-start.py*" }
}
$startHooksList += @{
    matcher = "startup|clear|resume"
    command = $startHookCmd
}
$settings["hooks"]["SessionStart"] = $startHooksList

# Register SessionEnd hook
$endHooksList = @()
if ($settings["hooks"].ContainsKey("SessionEnd")) {
    $existing = @($settings["hooks"]["SessionEnd"])
    $endHooksList = $existing | Where-Object { $_.command -notlike "*session-end.py*" }
}
$endHooksList += @{
    matcher = ".*"
    command = $endHookCmd
}
$settings["hooks"]["SessionEnd"] = $endHooksList

$jsonOut = $settings | ConvertTo-Json -Depth 10
Set-Content -LiteralPath $SettingsPath -Value $jsonOut -Encoding UTF8

Write-Host "Hooks successfully registered!" -ForegroundColor Green
Write-Host "  SessionStart -> $startHook"
Write-Host "  SessionEnd   -> $endHook"
