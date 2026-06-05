@echo off
setlocal
cd /d %~dp0
python --version >nul 2>&1
if errorlevel 1 (
  echo Python nao encontrado. Instale Python 3.11 ou 3.12 antes de continuar.
  pause
  exit /b 1
)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller --noconfirm --clean --onefile --name RendaDigitalIAPro --add-data "templates;templates" --add-data "static;static" desktop_launcher.py
if errorlevel 1 (
  echo Falha ao gerar o EXE.
  pause
  exit /b 1
)
echo.
echo EXE gerado em: dist\RendaDigitalIAPro.exe
echo Login inicial local: admin@local.com / MudeEssaSenha123!
pause
