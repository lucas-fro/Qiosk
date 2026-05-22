@echo off
setlocal

set "QIOSK_DIR=%~dp0"
set "QIOSK_EXE=%QIOSK_DIR%Qiosk.exe"
set "QIOSK_ICO=%QIOSK_DIR%qiosk.ico"

echo.
echo ===============================================================
echo  Instalador do Qiosk
echo ===============================================================
echo.
echo Pasta de instalacao: %QIOSK_DIR%
echo.

if not exist "%QIOSK_EXE%" (
    echo [ERRO] Qiosk.exe nao encontrado nesta pasta.
    echo Voce extraiu o conteudo do zip nesta pasta?
    echo.
    pause
    exit /b 1
)

echo Criando atalho "Qiosk" na area de trabalho...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desk = [Environment]::GetFolderPath('Desktop');" ^
  "$link = $ws.CreateShortcut(\"$desk\Qiosk.lnk\");" ^
  "$link.TargetPath = '%QIOSK_EXE%';" ^
  "$link.Arguments = '--config';" ^
  "$link.WorkingDirectory = '%QIOSK_DIR%';" ^
  "$link.IconLocation = '%QIOSK_ICO%,0';" ^
  "$link.Description = 'Qiosk - configuracao e inicio';" ^
  "$link.Save()"

if errorlevel 1 (
    echo.
    echo [ERRO] Falha ao criar o atalho.
    pause
    exit /b 1
)

echo.
echo [OK] Atalho "Qiosk" criado na area de trabalho.
echo Clique duas vezes nele para abrir o configurador.
echo.
pause
