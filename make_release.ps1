# =============================================================
# make_release.ps1 - Empacota a release pronta para upload no GitHub.
#
# Pre-requisito: rodar .\build.ps1 antes (precisa do dist\Qiosk\).
# Saida: Qiosk-v<VERSION>.zip na raiz, pronto pra enviar nas Releases.
#
# Uso:  .\make_release.ps1                 (default: v1.0)
#       .\make_release.ps1 -Version 1.1    (qualquer string)
# =============================================================

param(
    [string]$Version = "1.0"
)

Set-Location $PSScriptRoot

function Step($msg) {
    Write-Host ""
    Write-Host ">> $msg" -ForegroundColor Cyan
}

# --- Sanity checks -----------------------------------------------------

if (-not (Test-Path "dist\Qiosk\Qiosk.exe")) {
    Write-Host "ERRO: dist\Qiosk\Qiosk.exe nao encontrado." -ForegroundColor Red
    Write-Host "Rode .\build.ps1 primeiro." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path "release_files\install.bat")) {
    Write-Host "ERRO: release_files\install.bat nao encontrado." -ForegroundColor Red
    exit 1
}

$zipName = "Qiosk-v$Version.zip"
$stageDir = "dist\_release_stage"

# --- Limpeza ----------------------------------------------------------

# Mata Qiosk.exe se estiver rodando - se nao, Compress-Archive falha
# porque o DLL/zip interno do PyInstaller fica travado.
Step "Matando instancias do Qiosk (necessario para zipar)"
Get-Process -Name Qiosk -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500

Step "Limpando staging anterior"
Remove-Item -Recurse -Force $stageDir -ErrorAction SilentlyContinue
Remove-Item -Force $zipName -ErrorAction SilentlyContinue

# --- Copia o app + install.bat para staging --------------------------

Step "Copiando dist\Qiosk para staging"
New-Item -ItemType Directory -Path "$stageDir\Qiosk" | Out-Null
Copy-Item -Recurse -Path "dist\Qiosk\*" -Destination "$stageDir\Qiosk"

Step "Adicionando install.bat ao pacote"
Copy-Item -Path "release_files\install.bat" -Destination "$stageDir\Qiosk\install.bat"

# Remove artefatos do dev que nao devem ir pro usuario final
Remove-Item -Force "$stageDir\Qiosk\config.json" -ErrorAction SilentlyContinue
Remove-Item -Force "$stageDir\Qiosk\qiosk.log*" -ErrorAction SilentlyContinue

# --- Zipa --------------------------------------------------------------

Step "Compactando $zipName"
Compress-Archive -Path "$stageDir\Qiosk" -DestinationPath $zipName -Force

# Limpa staging
Remove-Item -Recurse -Force $stageDir

$zipSize = [math]::Round((Get-Item $zipName).Length / 1MB, 1)

Write-Host ""
Write-Host "Release pronto: $zipName ($zipSize MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Proximos passos (manuais):" -ForegroundColor Yellow
Write-Host "  1. Abra https://github.com/SEU_USUARIO/qiosk/releases/new"
Write-Host "  2. Tag version: 'v$Version'   |   Target: branch main"
Write-Host "  3. Release title: 'Qiosk v$Version'"
Write-Host "  4. Arrasta $zipName no campo 'Attach binaries'"
Write-Host "  5. Clica 'Publish release'"
Write-Host ""
Write-Host "Pronto: link publico do download fica disponivel imediatamente."
