<#
.SYNOPSIS
    Полный прогон канонического pipeline CS2 Tournament Report.

.DESCRIPTION
    1. audit prerequisite checks (сверка SHA-256 data/raw/ с базовым Audit v2)
    2. normalize sources       -> data/interim/
    3. build dimensions        -> data/processed/dim_*
    4. build rating events     -> data/processed/fact_player_rating_event.parquet
    5. build player-map fact   -> data/processed/fact_player_map.parquet
    6. build team-map fact     -> data/processed/fact_team_map.parquet
    7. build marts             -> data/processed/mart_*
    7b. audit team roster anomalies -> artifacts/validation/team_roster_anomalies.{json,md}
        (диагностика roster_size != 5, не блокирует сборку)
    8. generate/check player aliases -> data/private/player_aliases.csv (идемпотентно)
    9. build public data       -> data/public/*.json (+ leak check)
    10. build dashboard         -> dashboard.html (+ leak check)
    11. run validations         -> artifacts/validation/
    12. run publication leak checker (финальный, по data/public/ и dashboard.html)

.PARAMETER Clean
    Удалить data/interim, data/processed, data/public и dashboard.html перед
    запуском (НЕ удаляет data/raw и data/private — псевдонимы игроков должны
    оставаться стабильными между пересборками).

.PARAMETER Open
    Открыть dashboard.html в браузере по умолчанию после успешной сборки.

.PARAMETER SkipAudit
    Пропустить шаг 1 (сверку контрольных сумм data/raw/). Безопасно только
    если вы уверены, что data/raw/ не менялся с последнего Audit v2.

.PARAMETER NoDashboard
    Пересобрать только слой данных (шаги 1-9), не собирать dashboard.html.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File pipeline/run_pipeline.ps1 -Clean -Open
#>

param(
    [switch]$Clean,
    [switch]$Open,
    [switch]$SkipAudit,
    [switch]$NoDashboard
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

# Консоль Windows PowerShell 5.1 по умолчанию не в UTF-8 — без этого
# кириллица в Write-Host отображается нечитаемо (сама сборка pipeline от
# этого не страдает, только читаемость лога в консоли).
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Step {
    param([string]$Title, [string]$ScriptPath, [string[]]$Args = @())
    Write-Host ""
    Write-Host "=== $Title ===" -ForegroundColor Cyan
    & python $ScriptPath @Args
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ОШИБКА на шаге: $Title (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if ($Clean) {
    Write-Host "=== Clean: удаляю data/interim, data/processed, data/public, dashboard.html ===" -ForegroundColor Yellow
    foreach ($dir in @("data/interim", "data/processed", "data/public")) {
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    }
    if (Test-Path "dashboard.html") { Remove-Item -Force "dashboard.html" }
    Write-Host "data/raw/ и data/private/ сохранены (псевдонимы игроков не пересоздаются)." -ForegroundColor Yellow
}

if (-not $SkipAudit) {
    Invoke-Step "1. Audit prerequisite checks" "pipeline/audit_powerbi_logic.py"
} else {
    Write-Host "=== 1. Audit prerequisite checks: ПРОПУЩЕНО (-SkipAudit) ===" -ForegroundColor Yellow
}

Invoke-Step "2. Normalize sources"        "pipeline/normalize_sources.py"
Invoke-Step "3. Build dimensions"         "pipeline/build_dimensions.py"
Invoke-Step "4. Build rating events"      "pipeline/build_rating_events.py"
Invoke-Step "5. Build player-map fact"    "pipeline/build_fact_player_map.py"
Invoke-Step "6. Build team-map fact"      "pipeline/build_fact_team_map.py"
Invoke-Step "7. Build marts"              "pipeline/build_marts.py"
Invoke-Step "7b. Audit team roster anomalies (диагностика, не блокирует сборку)" "pipeline/audit_team_rosters.py"
Invoke-Step "8. Generate/check player aliases" "pipeline/generate_player_aliases.py"
Invoke-Step "9. Build public data (+ leak check)" "pipeline/build_public_data.py"

if (-not $NoDashboard) {
    Invoke-Step "10. Build dashboard (+ leak check)" "pipeline/build_dashboard.py"
} else {
    Write-Host "=== 10. Build dashboard: ПРОПУЩЕНО (-NoDashboard) ===" -ForegroundColor Yellow
}

Invoke-Step "11. Run validations" "pipeline/validate_pipeline.py"

Write-Host ""
Write-Host "=== 12. Финальная проверка утечек (data/public/) ===" -ForegroundColor Cyan
& python pipeline/check_publication_leaks.py data/public
if ($LASTEXITCODE -ne 0) {
    Write-Host "ОШИБКА: leak checker нашёл совпадения в data/public/" -ForegroundColor Red
    exit $LASTEXITCODE
}

if ((-not $NoDashboard) -and (Test-Path "dashboard.html")) {
    Write-Host ""
    Write-Host "=== 12. Финальная проверка утечек (dashboard.html) ===" -ForegroundColor Cyan
    & python pipeline/check_publication_leaks.py dashboard.html
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ОШИБКА: leak checker нашёл совпадения в dashboard.html" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "=== Pipeline завершён успешно ===" -ForegroundColor Green

if ($Open -and (-not $NoDashboard) -and (Test-Path "dashboard.html")) {
    Write-Host "Открываю dashboard.html ..."
    Start-Process (Join-Path $RootDir "dashboard.html")
}
