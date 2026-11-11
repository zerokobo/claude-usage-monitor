# Build ClaudeUsageMonitor.exe and, with -Install, copy it to %LOCALAPPDATA%\Programs and start it.
param([switch]$Install)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 が見つかりません。winget install Python.Python.3.12 で入れてください。' }
}
& $python -m pip install --disable-pip-version-check -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'requirements.txt のインストールに失敗しました' }

& $python -m unittest discover -v
if ($LASTEXITCODE -ne 0) { throw 'テストが失敗しました' }

& $python -m PyInstaller --noconfirm --onedir --windowed --name ClaudeUsageMonitor app.py
if ($LASTEXITCODE -ne 0) { throw 'ビルドに失敗しました' }
$built = Join-Path $PSScriptRoot 'dist\ClaudeUsageMonitor'
Write-Host "ビルド完了: $built\ClaudeUsageMonitor.exe"

if ($Install) {
    $target = Join-Path $env:LOCALAPPDATA 'Programs\ClaudeUsageMonitor'
    Get-Process -Name ClaudeUsageMonitor -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Copy-Item -LiteralPath $built -Destination $target -Recurse
    # Settings and history live in %LOCALAPPDATA%\ClaudeUsageMonitor and are kept across reinstalls.
    Start-Process -FilePath (Join-Path $target 'ClaudeUsageMonitor.exe')
    Write-Host "インストールして起動しました: $target"
}
