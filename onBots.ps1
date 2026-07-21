[CmdletBinding()]
param(
    [switch]$RunBot
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot '.venv'
$PythonPath = Join-Path $VenvPath 'Scripts\python.exe'
$EnvPath = Join-Path $ProjectRoot '.env'
$BuildTempPath = Join-Path $ProjectRoot '.tmp'

Set-Location $ProjectRoot

if (-not (Test-Path $PythonPath)) {
    Write-Host 'Creating isolated .venv environment...'
    py -3.14 -m venv $VenvPath
}

Write-Host 'Installing application and dependencies...'
New-Item -ItemType Directory -Force -Path $BuildTempPath | Out-Null
& $PythonPath -m pip install --upgrade pip
& $PythonPath -m pip install --editable "$ProjectRoot\config[dev]"
Remove-Item -LiteralPath $BuildTempPath -Recurse -Force -ErrorAction SilentlyContinue

if (-not (Test-Path $EnvPath)) {
    @(
        'TELEGRAM_BOT_TOKEN='
        'DATABASE_PATH=data/base.sqlite3'
        'APP_TIMEZONE=Europe/Moscow'
        'ADMIN_TELEGRAM_ID='
        'ADMIN_FULL_NAME='
        'DEFAULT_REMINDER_DAYS=14'
        'DEFAULT_REMINDER_TIME=09:00'
    ) | Set-Content -Encoding utf8 $EnvPath
    Write-Warning '.env created. Set TELEGRAM_BOT_TOKEN before starting the bot.'
}

& $PythonPath start.py init

if ($RunBot) {
    & $PythonPath start.py bot
} else {
    Write-Host 'Setup complete. Start with: .\onBots.ps1 -RunBot'
}
