@echo off
REM Double-click this on Windows to start the app.
REM It finds a real Python 3, then hands over to scripts\run_local.py.
setlocal

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python was not found.
  echo Install Python 3.10+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

%PY% "%~dp0scripts\run_local.py" %*
if errorlevel 1 pause
endlocal
