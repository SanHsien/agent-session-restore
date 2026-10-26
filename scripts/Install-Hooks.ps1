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

# Claude Code expects each event to hold matcher groups that contain a nested
# "hooks" list: { matcher, hooks = [ { type = "command"; command = "..." } ] }.
# A group is "ours" if any of its handlers (or the flat { command = ... } shape
# an earlier version of this script wrote) runs the given hook script.
function Test-GroupRunsScript {
    param($Group, [string]$ScriptPattern)
    if ($Group -isnot [System.Collections.IDictionary]) { return $false }
    if ($Group.Contains("command") -and $Group["command"] -like $ScriptPattern) { return $true }
    if ($Group.Contains("hooks")) {
        foreach ($handler in @($Group["hooks"])) {
            if ($handler -is [System.Collections.IDictionary] -and $handler.Contains("command") -and $handler["command"] -like $ScriptPattern) {
                return $true
            }
        }
    }
    return $false
}

function Merge-HookGroup {
    param($Settings, [string]$Event, [string]$ScriptPattern, [string]$Matcher, [string]$Command)
    $kept = @()
    if ($Settings["hooks"].Contains($Event)) {
        # Drop previous instances of this hook, keep everything else.
        foreach ($group in @($Settings["hooks"][$Event])) {
            if (-not (Test-GroupRunsScript -Group $group -ScriptPattern $ScriptPattern)) { $kept += ,$group }
        }
    }
    $kept += ,([ordered]@{
        matcher = $Matcher
        hooks   = @(
            [ordered]@{ type = "command"; command = $Command }
        )
    })
    $Settings["hooks"][$Event] = $kept
}

Merge-HookGroup -Settings $settings -Event "SessionStart" -ScriptPattern "*session-start.py*" -Matcher "startup|clear|resume" -Command $startHookCmd
Merge-HookGroup -Settings $settings -Event "SessionEnd" -ScriptPattern "*session-end.py*" -Matcher ".*" -Command $endHookCmd

$jsonOut = $settings | ConvertTo-Json -Depth 10
Set-Content -LiteralPath $SettingsPath -Value $jsonOut -Encoding UTF8

Write-Host "Hooks successfully registered!" -ForegroundColor Green
Write-Host "  SessionStart -> $startHook"
Write-Host "  SessionEnd   -> $endHook"
