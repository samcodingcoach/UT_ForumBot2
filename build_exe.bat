@echo off
REM ============================================================
REM  Build UT-Bot menjadi .exe dengan PyInstaller.
REM  Hasil: dist\UT-Bot.exe
REM ============================================================

setlocal
cd /d "%~dp0"

echo ============================================================
echo   Build UT-Bot.exe
echo ============================================================
echo.

REM --- Python ---
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if not defined PY (
    echo [ERROR] Python tidak ditemukan. Install Python dulu ^(lihat run.bat^).
    pause
    exit /b 1
)

REM --- Dependency aplikasi ---
echo [BUILD] Memastikan dependency aplikasi terpasang...
%PY% -m pip install -r requirements.txt

REM --- PyInstaller ---
%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [BUILD] Menginstall PyInstaller...
    %PY% -m pip install pyinstaller
)

REM --- Build ---
echo [BUILD] Membangun exe (memakai ut_bot.spec)...
%PY% -m PyInstaller --clean --noconfirm ut_bot.spec
if errorlevel 1 (
    echo [ERROR] Build gagal.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Selesai. File: dist\UT-Bot.exe
echo.
echo   PENTING sebelum menjalankan .exe di PC lain:
echo    1. Letakkan file .env DI SAMPING UT-Bot.exe (isi GEMINI_API_KEY).
echo    2. Saat pertama jalan, Chromium akan diunduh otomatis (butuh internet).
echo    3. session.json akan dibuat di folder yang sama setelah login.
echo ============================================================
pause >nul
endlocal
