[CmdletBinding()]
param([switch]$RunBot)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot '.venv'
$PythonPath = Join-Path $VenvPath 'Scripts\python.exe'
$EnvPath = Join-Path $ProjectRoot '.env'
$BuildTempPath = Join-Path $ProjectRoot '.tmp'
$InstallLog = Join-Path $BuildTempPath 'installer.log'
$TotalSteps = 5
$CurrentStep = 0

function Write-Banner {
    Clear-Host
    Write-Host ''
    Write-Host '  ==================================================' -ForegroundColor DarkMagenta
    Write-Host '     SENLA TEAM ASSISTANT' -ForegroundColor Magenta
    Write-Host '     Environment preparation and launch console' -ForegroundColor DarkGray
    Write-Host '  ==================================================' -ForegroundColor DarkMagenta
    Write-Host ''
}

function Write-Step([string]$Title) {
    $script:CurrentStep++
    $percent = [int](($script:CurrentStep - 1) / $TotalSteps * 100)
    Write-Progress -Activity 'Preparing Senla Team Assistant' -Status $Title -PercentComplete $percent
    $completed = '#' * $script:CurrentStep
    $pending = '-' * ($TotalSteps - $script:CurrentStep)
    Write-Host ('  [{0}{1}] ' -f $completed, $pending) -NoNewline -ForegroundColor Magenta
    Write-Host $Title -ForegroundColor White
}

function Write-Ok([string]$Text) {
    Write-Host '            + ' -NoNewline -ForegroundColor Cyan
    Write-Host $Text -ForegroundColor Gray
}

function Write-Notice([string]$Text) {
    Write-Host '            ! ' -NoNewline -ForegroundColor Yellow
    Write-Host $Text -ForegroundColor Yellow
}

function Stop-Installer([string]$Text) {
    Write-Progress -Activity 'Preparing Senla Team Assistant' -Completed
    Write-Host ''
    Write-Host '  ==================================================' -ForegroundColor DarkRed
    Write-Host '     SETUP FAILED' -ForegroundColor Red
    Write-Host ('     ' + $Text) -ForegroundColor White
    Write-Host '     Details: .tmp\installer.log' -ForegroundColor DarkGray
    Write-Host '  ==================================================' -ForegroundColor DarkRed
    exit 1
}

function Invoke-Hidden(
    [scriptblock]$Action,
    [string]$CommandText,
    [string]$ErrorText
) {
    Write-Host '            > ' -NoNewline -ForegroundColor DarkMagenta
    Write-Host $CommandText -NoNewline -ForegroundColor Blue
    try {
        & $Action *> $InstallLog
        if ($LASTEXITCODE -ne 0) { throw 'command failed' }
        Write-Host '  OK' -ForegroundColor Cyan
    }
    catch {
        Write-Host '  ERROR' -ForegroundColor Red
        Stop-Installer $ErrorText
    }
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force -Path $BuildTempPath | Out-Null
Set-Content -Path $InstallLog -Value '' -Encoding utf8
Write-Banner

Write-Step 'Checking Python environment'
if (-not (Test-Path $PythonPath)) {
    Invoke-Hidden { py -3.14 -m venv $VenvPath } 'py -3.14 -m venv .venv' 'Unable to create the virtual environment.'
    Write-Ok 'Virtual environment created'
} else {
    Write-Ok 'Virtual environment is ready'
}

Write-Step 'Updating package installer'
Invoke-Hidden { & $PythonPath -m pip install --upgrade pip } 'python -m pip install --upgrade pip' 'Unable to update pip.'
Write-Ok 'pip updated'

Write-Step 'Installing application components'
Invoke-Hidden { & $PythonPath -m pip install --editable "$ProjectRoot\config[dev]" } 'python -m pip install --editable config[dev]' 'Unable to install dependencies.'
Write-Host '            > ' -NoNewline -ForegroundColor DarkMagenta
Write-Host 'verify aiogram integrity' -NoNewline -ForegroundColor Blue
& $PythonPath -c "import inspect; from aiogram.client.session import aiohttp; assert 'core.token' not in inspect.getsource(aiohttp)" *> $InstallLog
if ($LASTEXITCODE -ne 0) {
    Write-Host '  REPAIR' -ForegroundColor Yellow
    Write-Notice 'Damaged aiogram installation detected; repairing'
    Invoke-Hidden {
        & $PythonPath -m pip install --force-reinstall --no-deps aiogram==3.30.0
    } 'python -m pip install --force-reinstall aiogram==3.30.0' 'Unable to repair aiogram.'
} else {
    Write-Host '  OK' -ForegroundColor Cyan
}
Write-Ok 'Components installed'

Write-Step 'Preparing configuration'
if (-not (Test-Path $EnvPath)) {
    @(
        'TELEGRAM_BOT_TOKEN='
        'ONBOARDING_PASSWORD='
        'DATABASE_PATH=data/base.sqlite3'
        'APP_TIMEZONE=Europe/Moscow'
        'ADMIN_TELEGRAM_ID='
        'ADMIN_FULL_NAME='
        'DEFAULT_REMINDER_DAYS=14'
        'DEFAULT_REMINDER_TIME=09:00'
    ) | Set-Content -Encoding utf8 $EnvPath
    Write-Notice '.env created - set TELEGRAM_BOT_TOKEN and ONBOARDING_PASSWORD'
} else {
    Write-Ok '.env found'
}

Write-Step 'Initializing system core'
Invoke-Hidden { & $PythonPath start.py init } 'python start.py init' 'Unable to initialize the application.'
Write-Ok 'Core and database are ready'

Write-Progress -Activity 'Preparing Senla Team Assistant' -Completed
Write-Host ''
Write-Host '  ==================================================' -ForegroundColor Magenta
Write-Host '     READY TO WORK' -ForegroundColor White -BackgroundColor DarkMagenta
Write-Host '     Senla Team Assistant is fully prepared.' -ForegroundColor Cyan
Write-Host '     Core: ready | Database: ready | Config: ready' -ForegroundColor Gray
Write-Host '  ==================================================' -ForegroundColor Magenta
Write-Host ''

if ($RunBot) {
    Write-Host '  [RUN] ' -NoNewline -ForegroundColor Magenta
    Write-Host 'python start.py bot' -NoNewline -ForegroundColor Blue
    Write-Host '  ACTIVE' -ForegroundColor Cyan
    try {
        & $PythonPath start.py bot
    }
    catch {
        Stop-Installer 'Application stopped with an error.'
    }
} else {
    Write-Host '  The application is ready to work.' -ForegroundColor Cyan
    Write-Host '  Start command: .\onBots.ps1 -RunBot' -ForegroundColor White
    Write-Host ''
}
