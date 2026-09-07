@echo off
setlocal

REM Fetches the newest release and hands over to start.bat, which stops the old
REM container, rebuilds the image and starts the new one. Nothing here talks to
REM Docker directly, so the update path cannot drift away from the start path.

cd /d "%~dp0"

echo Updating PBF Forge...

git --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Git is not installed, so this folder cannot update itself.
    echo Get the newest release from:
    echo   https://github.com/MartinHoblisch/pbf-forge/releases/latest
    pause
    exit /b 1
)

if not exist ".git" (
    echo.
    echo ERROR: This folder is not a Git checkout, so there is nothing to pull.
    echo Get the newest release from:
    echo   https://github.com/MartinHoblisch/pbf-forge/releases/latest
    pause
    exit /b 1
)

REM Your own edits are never overwritten silently. Config and data live outside
REM the tracked files, so this only ever trips on changed source.
git diff --quiet
if errorlevel 1 goto local_changes
git diff --cached --quiet
if errorlevel 1 goto local_changes

REM --ff-only: an update may fast-forward, never turn into a merge nobody asked
REM for. A diverged branch stops here instead of producing conflicts.
git pull --ff-only
if errorlevel 1 (
    echo.
    echo ERROR: Could not fast-forward to the newest release.
    echo See the message above, or reinstall from:
    echo   https://github.com/MartinHoblisch/pbf-forge/releases/latest
    pause
    exit /b 1
)

echo.
echo Update fetched. Starting PBF Forge...
call start.bat
endlocal
exit /b 0

:local_changes
echo.
echo ERROR: This folder has local changes to tracked files.
echo Commit or discard them, then run this script again:
echo   git stash
pause
exit /b 1
