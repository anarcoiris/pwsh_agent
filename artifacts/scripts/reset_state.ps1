<#
.SYNOPSIS
  Thin wrapper around scripts/reset_state.py. Archives + purges agent runtime
  state for a clean installation, using the repo's venv interpreter.

.EXAMPLE
  .\scripts\reset_state.ps1                 # dry-run, mode keep-identity
  .\scripts\reset_state.ps1 -Apply          # archive + purge (prompts)
  .\scripts\reset_state.ps1 -Mode keep-identity-wipe-db -Apply -Yes
  .\scripts\reset_state.ps1 -Mode factory-reset -Apply
#>
[CmdletBinding()]
param(
    [ValidateSet("keep-identity", "keep-identity-wipe-db", "factory-reset")]
    [string]$Mode = "keep-identity",
    [switch]$Apply,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "reset_state.py"

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
} else {
    $python = "python"
}

$pyArgs = @($script, "--mode", $Mode)
if ($Apply) { $pyArgs += "--apply" }
if ($Yes)   { $pyArgs += "--yes" }

& $python @pyArgs
exit $LASTEXITCODE
