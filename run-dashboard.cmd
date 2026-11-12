@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0" || exit /b 1

if defined HOTEL_PYTHON goto custom_python
set "python_check=import sys; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 14))"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "%python_check%" >nul 2>&1
    if not errorlevel 1 goto project_python
)
for %%V in (3.12 3.13 3.14 3.11) do (
    py -%%V -c "%python_check%" >nul 2>&1
    if not errorlevel 1 (
        set "python_version=%%V"
        goto python_launcher
    )
)
python -c "%python_check%" >nul 2>&1
if not errorlevel 1 goto python_on_path
echo Python 3.11-3.14 is required. Install Python for Windows from https://www.python.org/downloads/windows/
set "launch_status=1"
goto finish

:custom_python
"%HOTEL_PYTHON%" -X utf8 tools\launch_dashboard.py %*
goto collect_status

:project_python
".venv\Scripts\python.exe" -X utf8 tools\launch_dashboard.py %*
goto collect_status

:python_launcher
py -%python_version% -X utf8 tools\launch_dashboard.py %*
goto collect_status

:python_on_path
python -X utf8 tools\launch_dashboard.py %*

:collect_status
set "launch_status=%errorlevel%"

:finish
popd
exit /b %launch_status%
