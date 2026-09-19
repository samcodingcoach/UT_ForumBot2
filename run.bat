@echo off
REM ============================================================
REM  UT Moodle Forum Auto-Grader & Auto-Reply Bot - Launcher
REM  Cek semua kebutuhan; unduh & install otomatis bila belum ada.
REM  Klik dua kali file ini untuk menjalankan bot.
REM ============================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   UT Moodle Forum Auto-Grader Bot - Pemeriksaan Kebutuhan
echo ============================================================
echo.

REM ============================================================
REM  1. PYTHON
REM ============================================================
call :find_python
if defined PY goto :python_ok

echo [SETUP] Python tidak ditemukan. Mencoba menginstall otomatis...
echo.

REM --- Coba via winget (bawaan Windows 10/11) ---
where winget >nul 2>nul
if %errorlevel%==0 (
    echo [SETUP] Menginstall Python 3.12 via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo [SETUP] Instalasi selesai. Menyegarkan PATH...
    call :refresh_path
    call :find_python
    if defined PY goto :python_ok
)

REM --- Fallback: unduh installer resmi Python ---
echo [SETUP] winget tidak tersedia / gagal. Mengunduh installer Python...
set "PY_URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
set "PY_INSTALLER=%TEMP%\python-installer.exe"
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%' } catch { exit 1 }"
if not exist "%PY_INSTALLER%" (
    echo [ERROR] Gagal mengunduh installer Python.
    echo         Install manual dari https://www.python.org/downloads/
    echo         Jangan lupa centang "Add Python to PATH".
    pause
    exit /b 1
)

echo [SETUP] Menjalankan installer Python (silent, Add to PATH)...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
del "%PY_INSTALLER%" >nul 2>nul
call :refresh_path
call :find_python
if not defined PY (
    echo [ERROR] Python terpasang tapi belum terbaca. Tutup jendela ini,
    echo         lalu jalankan run.bat sekali lagi.
    pause
    exit /b 1
)

:python_ok
echo [OK] Python terdeteksi: %PY%
%PY% --version
echo.

REM ============================================================
REM  2. FILE .env
REM ============================================================
if not exist ".env" (
    if exist ".env.example" (
        echo [SETUP] .env belum ada. Membuat dari .env.example...
        copy /y ".env.example" ".env" >nul
        echo [PENTING] Buka file .env dan isi GEMINI_API_KEY Anda,
        echo           lalu jalankan run.bat lagi.
        notepad ".env"
        pause
        exit /b 0
    ) else (
        echo [ERROR] .env dan .env.example tidak ada. Tidak bisa lanjut.
        pause
        exit /b 1
    )
)
echo [OK] File .env ditemukan.
echo.

REM ============================================================
REM  3. DEPENDENCY PYTHON
REM ============================================================
%PY% -c "import playwright, google.genai, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Dependency belum lengkap. Menginstall dari requirements.txt...
    %PY% -m pip install --upgrade pip
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Gagal menginstall dependency. Periksa koneksi internet.
        pause
        exit /b 1
    )
) else (
    echo [OK] Dependency Python sudah lengkap.
)
echo.

REM ============================================================
REM  4. BROWSER CHROMIUM (Playwright)
REM ============================================================
%PY% -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()" >nul 2>nul
if errorlevel 1 (
    echo [SETUP] Browser Chromium belum terpasang. Mengunduh...
    %PY% -m playwright install chromium
    if errorlevel 1 (
        echo [ERROR] Gagal mengunduh Chromium. Periksa koneksi internet.
        pause
        exit /b 1
    )
) else (
    echo [OK] Browser Chromium siap.
)
echo.

REM ============================================================
REM  5. JALANKAN BOT
REM ============================================================
echo ============================================================
echo   Semua kebutuhan siap. Menjalankan bot...
echo ============================================================
echo.
%PY% main.py

echo.
echo ============================================================
echo   Bot selesai. Tekan tombol apa saja untuk menutup jendela.
echo ============================================================
pause >nul
endlocal
exit /b 0


REM ============================================================
REM  SUBRUTIN
REM ============================================================
:find_python
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py"
)
REM Cek juga lokasi instalasi user default bila PATH belum tersegarkan
if not defined PY (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%D\python.exe" set "PY=%%D\python.exe"
    )
)
exit /b 0

:refresh_path
REM Muat ulang PATH dari registry (mesin + user) tanpa perlu buka CMD baru
for /f "tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul ^| findstr /i "Path"') do set "SYS_PATH=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| findstr /i "Path"') do set "USR_PATH=%%B"
set "PATH=%SYS_PATH%;%USR_PATH%"
exit /b 0
