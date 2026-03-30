@echo off
setlocal enabledelayedexpansion
title Elevate Learning Platform

cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "AI_SERVICE_URL=http://127.0.0.1:7860"
set "AI_START_SCRIPT=ai\start.bat"
set "AI_LOG_FILE=%cd%\ai\topic_ai_service.log"

echo.
echo  ================================================
echo   Elevate - AI Adaptive Learning Platform
echo  ================================================
echo.

:: ── Check system Python ──────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Install Python 3.11+ from https://python.org and add it to PATH.
    echo.
    pause
    exit /b 1
)

:: ── Validate/repair virtual environment ─────────────────────────────────────
set "NEEDS_SETUP=0"
if not exist "%VENV_PY%" (
    set "NEEDS_SETUP=1"
) else (
    "%VENV_PY%" -c "import pip._internal; from flask import Flask; import flask_sqlalchemy" >nul 2>&1
    if errorlevel 1 (
        echo  [WARN] Existing virtual environment is broken or incomplete.
        set "NEEDS_SETUP=1"
    )
)

if "%NEEDS_SETUP%"=="1" (
    echo  [SETUP] Preparing virtual environment...
    echo.

    if exist ".venv" (
        echo  [SETUP] Removing previous broken .venv...
        rmdir /s /q ".venv"
    )

    echo  [1/4] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] Could not create virtual environment.
        pause
        exit /b 1
    )

    echo  [2/4] Bootstrapping pip and installing dependencies...
    "%VENV_PY%" -m ensurepip --upgrade >nul 2>&1
    "%VENV_PY%" -m pip --version >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] pip bootstrap failed in .venv.
        pause
        exit /b 1
    )

    "%VENV_PY%" -m pip install --upgrade pip setuptools wheel
    if errorlevel 1 (
        echo  [ERROR] Failed to upgrade pip/setuptools/wheel.
        pause
        exit /b 1
    )

    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo  [ERROR] Dependency installation failed.
        pause
        exit /b 1
    )

    "%VENV_PY%" -c "import pip._internal; from flask import Flask; import flask_sqlalchemy" >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Package verification failed after installation.
        echo  [HINT] The environment has inconsistent package files. Re-run start.bat.
        pause
        exit /b 1
    )

    echo  [3/4] Verifying installed runtime...
    if not exist "scripts\startup_healthcheck.py" (
        echo  [ERROR] scripts\startup_healthcheck.py is missing.
        pause
        exit /b 1
    )

    echo.
    echo  [SETUP] Setup complete!
) else (
    echo  [INFO] Virtual environment found and healthy.
)

echo  [CHECK] Running startup healthcheck...
"%VENV_PY%" scripts\startup_healthcheck.py
if errorlevel 1 (
    echo  [ERROR] Startup health check failed.
    pause
    exit /b 1
)

powershell -NoProfile -Command "try { Invoke-RestMethod -Uri '%AI_SERVICE_URL%/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    if not exist "%AI_START_SCRIPT%" (
        echo  [WARN] %AI_START_SCRIPT% is missing.
        echo  [WARN] Skipping Topic AI startup. Teacher AI generation may be unavailable.
    ) else (
        echo  [AI] Ensuring Topic AI environment via %AI_START_SCRIPT% ...
        call "%AI_START_SCRIPT%" --ensure-env
        if errorlevel 1 (
            echo  [WARN] Topic AI environment setup failed.
            echo  [WARN] Skipping Topic AI startup. Teacher AI generation may be unavailable.
        ) else (
            echo  [AI] Starting Topic AI service at %AI_SERVICE_URL% ...
            if exist "%AI_LOG_FILE%" del /q "%AI_LOG_FILE%" >nul 2>&1
            start "" /B cmd /c "cd /d ""%cd%\ai"" && call start.bat --serve >> ""%AI_LOG_FILE%"" 2>&1"

            powershell -NoProfile -Command "$ready=$false; for($i=0; $i -lt 240; $i++){ try { Invoke-RestMethod -Uri '%AI_SERVICE_URL%/health' -TimeoutSec 2 | Out-Null; $ready=$true; break } catch {}; Start-Sleep -Milliseconds 500 }; if($ready){ exit 0 } else { exit 1 }"
            if errorlevel 1 (
                echo  [WARN] Topic AI service did not become healthy in time. Teacher AI generation may be unavailable.
                if exist "%AI_LOG_FILE%" (
                    echo  [AI] Last log lines from %AI_LOG_FILE%:
                    powershell -NoProfile -Command "Get-Content -Path '%AI_LOG_FILE%' -Tail 40"
                )
            ) else (
                powershell -NoProfile -Command "try { Invoke-RestMethod -Uri '%AI_SERVICE_URL%/warmup' -Method POST -TimeoutSec 45 | Out-Null; exit 0 } catch { exit 1 }"
                if errorlevel 1 (
                    echo  [AI] Warmup timed out or was skipped. First AI request may be slow.
                ) else (
                    echo  [AI] Topic AI service healthy and warmed.
                )
            )
        )
    )
) else (
    echo  [AI] Topic AI service already running.
)

:: ── Launch ───────────────────────────────────────────────────────────────────
echo.
echo  ------------------------------------------------
echo   App running at:  http://127.0.0.1:5000
echo   Topic AI API :   %AI_SERVICE_URL%
echo  ------------------------------------------------
echo   Student  :  student@elevate.com  /  student123
echo   Teacher  :  teacher@elevate.com  /  teacher123
echo   Admin    :  admin@elevate.com    /  admin123
echo  ------------------------------------------------
echo   Press Ctrl+C to stop.
echo.

:: Open browser after a short delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"

"%VENV_PY%" -m backend.run

pause
