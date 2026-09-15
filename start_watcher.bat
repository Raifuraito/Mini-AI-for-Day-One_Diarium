@echo off
REM start_watcher.bat -- starts the journal watcher in the background (silent).
REM Double-click any time to start it, OR make it run at every login:
REM press Win+R, type  shell:startup , Enter, then right-drag this file into
REM that folder and choose "Create shortcuts here."
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw watcher.py
) else (
    start "" /min python watcher.py
)
