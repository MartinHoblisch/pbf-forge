@echo off
setlocal enabledelayedexpansion

echo Starting PBF Forge...

REM Check if Docker Desktop is running
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready

echo Docker Desktop is not running. Starting automatically...

set "DOCKER_EXE="
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" set "DOCKER_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%LocalAppData%\Programs\Docker\Docker\Docker Desktop.exe" set "DOCKER_EXE=%LocalAppData%\Programs\Docker\Docker\Docker Desktop.exe"

if "!DOCKER_EXE!"=="" (
    echo.
    echo ERROR: Docker Desktop not found.
    echo Please install Docker Desktop and try again.
    echo Download: https://www.docker.com/
    echo.
    pause
    exit /b 1
)

start "" "!DOCKER_EXE!"

echo Waiting for Docker Desktop...
:wait_loop
timeout /t 4 /nobreak >nul
docker info >nul 2>&1
if errorlevel 1 goto wait_loop
echo Docker Desktop is ready.

:docker_ready

REM Create user-config.json if missing (with migration from .env if present)
if not exist "config\user-config.json" (
    powershell -ExecutionPolicy Bypass -NoProfile -Command ^
        "$env_content = Get-Content '.env' -Raw -ErrorAction SilentlyContinue;" ^
        "$data_dir = if ($env_content -match 'DATA_DIR=([^\r\n]+)') { $matches[1].Trim() } else { '' };" ^
        "$configured = [bool]$data_dir;" ^
        "$cfg = [ordered]@{ configured = $configured; host_data_dir = $data_dir; pending_restart = $false };" ^
        "$cfg | ConvertTo-Json | Set-Content -Encoding utf8 'config\user-config.json'"
)

REM Read host_data_dir from user-config.json (empty = fallback to .\data)
set "DATA_DIR="
for /f "usebackq delims=" %%D in (`powershell -ExecutionPolicy Bypass -NoProfile -Command "(Get-Content 'config\user-config.json' -Raw | ConvertFrom-Json).host_data_dir"`) do set "DATA_DIR=%%D"
if "!DATA_DIR!"=="" set "DATA_DIR=.\data"

REM Create data directory before Docker creates it as root
if not exist "!DATA_DIR!" mkdir "!DATA_DIR!" 2>nul

REM Restart Docker with current DATA_DIR
docker compose -f docker-compose.yml -f docker-compose.windows.yml down --remove-orphans 2>nul
docker compose -f docker-compose.yml -f docker-compose.windows.yml up --build -d

if errorlevel 1 (
    echo ERROR: Docker Compose failed.
    pause
    exit /b 1
)

echo.
echo PBF Forge running at: http://localhost:8000
echo To stop: run stop.bat or "docker compose down"
echo.

REM Open browser (short delay to let the server start)
timeout /t 2 /nobreak >nul
start http://localhost:8000

endlocal
