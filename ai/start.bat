@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "MODE=serve"
if /I "%~1"=="--ensure-env" set "MODE=ensure"
if /I "%~1"=="--serve" set "MODE=serve"

set "PROJECT_ROOT=%~dp0.."
for %%I in ("%PROJECT_ROOT%") do set "PROJECT_ROOT=%%~fI"

set "AI_VENV=%PROJECT_ROOT%\.venv-ai"
set "AI_PY=%AI_VENV%\Scripts\python.exe"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

set "NEEDS_CREATE=0"
if not exist "%AI_PY%" set "NEEDS_CREATE=1"

if "%NEEDS_CREATE%"=="1" (
	echo [AI-SETUP] Creating .venv-ai ...
	if exist "%AI_VENV%" rmdir /s /q "%AI_VENV%"

	python -m venv "%AI_VENV%"
	if errorlevel 1 (
		echo [AI-SETUP] ERROR: failed to create .venv-ai
		exit /b 1
	)

	"%AI_PY%" -m ensurepip --upgrade >nul 2>&1
)

"%AI_PY%" -m pip --version >nul 2>&1
if errorlevel 1 (
	"%AI_PY%" -m ensurepip --upgrade >nul 2>&1
)

"%AI_PY%" -c "import uvicorn, fastapi, requests, dotenv" >nul 2>&1
if errorlevel 1 (
	echo [AI-SETUP] Installing AI dependencies...
	"%AI_PY%" -m pip install --upgrade pip setuptools wheel
	if errorlevel 1 (
		echo [AI-SETUP] ERROR: failed to bootstrap pip tooling
		exit /b 1
	)

	"%AI_PY%" -m pip install -r requirements.txt
	if errorlevel 1 (
		echo [AI-SETUP] ERROR: failed to install ai\requirements.txt
		exit /b 1
	)
)

"%AI_PY%" -c "import uvicorn, fastapi, requests, dotenv, torch, transformers, peft, accelerate, datasets, safetensors" >nul 2>&1
if errorlevel 1 (
	echo [AI-SETUP] ERROR: AI dependency verification failed
	exit /b 1
)

if /I "%MODE%"=="ensure" (
	echo [AI-SETUP] .venv-ai ready.
	exit /b 0
)

if "%PORT%"=="" set PORT=7860

echo [AI] Running Topic AI service on http://127.0.0.1:%PORT%
"%AI_PY%" -m uvicorn app:app --host 127.0.0.1 --port %PORT%
