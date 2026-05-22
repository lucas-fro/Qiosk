# =============================================================
# build.ps1 - Compila o Qiosk.exe e cria atalho na area de trabalho.
#
# Uso:  .\build.ps1                  (compila + cria atalho)
#       .\build.ps1 -NoShortcut      (so compila)
#       .\build.ps1 -SkipDeps        (pula pip install, build mais rapido)
# =============================================================

param(
    [switch]$NoShortcut,
    [switch]$SkipDeps
)

# Nao usamos ErrorActionPreference=Stop globalmente porque ferramentas
# nativas como PyInstaller escrevem INFO no stderr, e o PowerShell 5.1
# interpreta isso como erro fatal. Em vez disso, checamos $LASTEXITCODE.

Set-Location $PSScriptRoot

function Step($msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}

function Check-Exit($desc) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERRO: $desc falhou (exit code $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
}

# --- 1. Checar Python ---------------------------------------------------
Step "Checando Python"
$pyVer = (& python --version 2>&1)
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERRO: Python nao encontrado no PATH." -ForegroundColor Red
    Write-Host "Instale em https://python.org (marque 'Add to PATH')." -ForegroundColor Yellow
    exit 1
}
Write-Host "   $pyVer"

# --- 2. Instalar dependencias ------------------------------------------
if (-not $SkipDeps) {
    Step "Instalando dependencias (pode demorar na primeira vez)"
    python -m pip install --quiet --upgrade pip
    Check-Exit "pip install --upgrade pip"
    python -m pip install --quiet -r requirements.txt
    Check-Exit "pip install -r requirements.txt"
} else {
    Write-Host ">> Pulando pip install (-SkipDeps)" -ForegroundColor DarkGray
}

# --- 3. Checar o icone -------------------------------------------------
if (-not (Test-Path "assets\qiosk.ico")) {
    Write-Host "ERRO: assets\qiosk.ico nao encontrado." -ForegroundColor Red
    Write-Host "Substitua/gere um arquivo .ico (multi-resolucao) em assets\." -ForegroundColor Yellow
    exit 1
}

# --- 4. Matar instancias rodando (senao PyInstaller falha) ------------
Step "Matando instancias antigas do Qiosk"
Get-Process -Name Qiosk -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

# --- 5. Compilar -------------------------------------------------------
Step "Compilando Qiosk.exe (PyInstaller - pode levar 1 min)"
python -m PyInstaller --noconfirm --windowed --name Qiosk `
    --icon=assets/qiosk.ico `
    --add-data "assets/qiosk.ico;assets" `
    --add-data "assets/logomarca-qiosk-png.png;assets" `
    qiosk.py | Out-Null
Check-Exit "PyInstaller"

# Copia o .ico standalone para o atalho usar (Windows precisa de .ico real,
# nao de png; o IconLocation do .lnk nao aceita o .ico embutido no .exe
# em alguns casos de cache).
Copy-Item -Force assets\qiosk.ico dist\Qiosk\qiosk.ico

$exePath = Join-Path $PWD "dist\Qiosk\Qiosk.exe"
Write-Host "   Gerado: $exePath" -ForegroundColor Green

# --- 6. Atalho na area de trabalho ------------------------------------
if (-not $NoShortcut) {
    Step "Criando atalho 'Qiosk' na area de trabalho"
    $desktop = [Environment]::GetFolderPath('Desktop')
    $icoPath = Join-Path $PWD "dist\Qiosk\qiosk.ico"
    $workDir = Join-Path $PWD "dist\Qiosk"

    Remove-Item -Force "$desktop\Qiosk.lnk" -ErrorAction SilentlyContinue
    $ws = New-Object -ComObject WScript.Shell
    $link = $ws.CreateShortcut("$desktop\Qiosk.lnk")
    $link.TargetPath = $exePath
    $link.Arguments = "--config"
    $link.WorkingDirectory = $workDir
    $link.IconLocation = "$icoPath,0"
    $link.Description = "Qiosk - configuracao e inicio"
    $link.Save()
    Write-Host "   Atalho criado: $desktop\Qiosk.lnk" -ForegroundColor Green
}

Write-Host ""
Write-Host "Build concluido!" -ForegroundColor Green
Write-Host "Da um duplo-clique no atalho 'Qiosk' da area de trabalho para iniciar."
