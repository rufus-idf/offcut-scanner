@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
)

echo Building Offcut Scanner Windows executable...
"%PYTHON_EXE%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name OffcutScanner ^
  offcut_scanner_app.py

if errorlevel 1 (
  echo.
  echo Build failed.
  echo If PyInstaller is missing, run:
  echo   "%PYTHON_EXE%" -m pip install pyinstaller
  exit /b 1
)

if not exist "dist\OffcutScanner\captures" (
  mkdir "dist\OffcutScanner\captures"
)

if exist "calibration.json" (
  copy /Y "calibration.json" "dist\OffcutScanner\calibration.json" >nul
  echo Copied calibration.json into dist\OffcutScanner\
) else (
  echo calibration.json was not found in the repo root.
  echo Place your calibration.json next to OffcutScanner.exe after the build.
)

echo.
echo Build complete.
echo Run: dist\OffcutScanner\OffcutScanner.exe
echo You can create a desktop shortcut to that .exe.
