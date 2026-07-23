[CmdletBinding()]
param([switch]$RunBot)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPath = Join-Path $ProjectRoot '.venv'
$PythonPath = Join-Path $VenvPath 'Scripts\python.exe'
$EnvPath = Join-Path $ProjectRoot '.env'
$BuildTempPath = Join-Path $ProjectRoot '.temp'
$LogPath = Join-Path $ProjectRoot 'logs'
$InstallLog = Join-Path $LogPath 'installer.log'
$TotalSteps = 5
$CurrentStep = 0
$PythonCommand = $null

function Write-Banner {
    Clear-Host
    Write-Host ''
    Write-Host '  Corporate Assistant' -ForegroundColor Magenta
    Write-Host '  -------------------' -ForegroundColor DarkMagenta
    Write-Host '  Setup and launch' -ForegroundColor DarkGray
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
    Write-Host '     Details: logs\installer.log' -ForegroundColor DarkGray
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
New-Item -ItemType Directory -Force -Path $LogPath | Out-Null
$env:TEMP = $BuildTempPath
$env:TMP = $BuildTempPath
$env:PIP_CACHE_DIR = Join-Path $BuildTempPath 'pip-cache'
Set-Content -Path $InstallLog -Value '' -Encoding utf8
Write-Banner

Write-Step 'Checking Python environment'
if ((Get-Command py -ErrorAction SilentlyContinue) -and
    ((& py -3 -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15)" 2>$null) -eq $null) -and
    ($LASTEXITCODE -eq 0)) {
    $PythonCommand = { py -3 @args }
}
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and
    ((& python -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15)" 2>$null) -eq $null) -and
    ($LASTEXITCODE -eq 0)) {
    $PythonCommand = { python @args }
}
else {
    Write-Notice 'Compatible Python 3.11-3.14 is not installed'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Invoke-Hidden {
            winget install --id Python.Python.3.13 --exact --silent `
                --accept-package-agreements --accept-source-agreements
        } 'winget install Python 3.13' 'Unable to install Python automatically.'
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User')
        $PythonCommand = { py -3.13 @args }
    }
    else {
        Stop-Installer 'Install Python 3.11-3.14 or install winget, then run setup again.'
    }
}

if (-not (Test-Path $PythonPath)) {
    if (Test-Path $VenvPath) {
        $ResolvedVenv = [System.IO.Path]::GetFullPath($VenvPath)
        $ExpectedVenv = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot '.venv'))
        if ($ResolvedVenv -ne $ExpectedVenv) {
            Stop-Installer 'Unsafe virtual environment path.'
        }
        Remove-Item -LiteralPath $ResolvedVenv -Recurse -Force
        Write-Notice 'Incomplete virtual environment removed'
    }
    Invoke-Hidden { & $PythonCommand -m venv $VenvPath } 'python -m venv .venv' 'Unable to create the virtual environment.'
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
        'DATABASE_URL=sqlite:///data/base.sqlite3'
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
Write-Host '  Ready' -ForegroundColor Magenta
Write-Host '  -----' -ForegroundColor DarkMagenta
Write-Host '  Core        ready' -ForegroundColor Gray
Write-Host '  Database    ready' -ForegroundColor Gray
Write-Host '  Config      ready' -ForegroundColor Gray
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
