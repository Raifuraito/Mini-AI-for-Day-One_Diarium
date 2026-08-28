@echo off
REM start_setup.bat
REM
REM Double-click this file to set up the journal RAG app. It checks
REM whether Python is installed, helps you get it if it's not, and then
REM launches the setup wizard in your browser. This is the ONLY thing
REM you should need to double-click to get started -- everything else
REM happens inside the wizard page itself.

title Journal RAG - Setup
echo.
echo   Journal RAG - Setup
echo   ===================
echo.

REM --- Check whether Python is available at all ---
REM "where" is the Windows equivalent of "which" -- it searches the same
REM PATH that any program (including this one) uses to find executables.
REM Redirecting output to nul keeps this check silent either way; only
REM the exit code (errorlevel) is used to decide what happens next.
where python >nul 2>nul
if %errorlevel% neq 0 (
    goto :no_python
)

REM --- Python was found -- but "python" can exist on PATH as a harmless
REM stub that just opens the Microsoft Store, on some Windows installs
REM that never had Python actually set up. Confirm it really runs before
REM trusting it.
python --version >nul 2>nul
if %errorlevel% neq 0 (
    goto :no_python
)

echo   Found Python. Starting the setup wizard...
echo.
echo   Your browser should open automatically in a moment.
echo   If it doesn't, go to: http://localhost:5050
echo.
echo   Leave this window open while you use the setup page.
echo   Closing this window will stop the setup wizard.
echo.

python "%~dp0setup_wizard.py"
goto :end

:no_python
echo   Python wasn't found on this computer.
echo.
echo   This app needs Python to run (it's free, and only takes a couple
echo   of minutes to install). Opening the download page for you now --
echo.
echo   IMPORTANT: on the first screen of the installer, check the box
echo   that says "Add python.exe to PATH" before clicking Install.
echo   If you miss that step, this launcher won't be able to find
echo   Python afterwards either.
echo.
echo   Once it's installed, come back and double-click this file again.
echo.
start https://www.python.org/downloads/
pause
goto :end

:end
echo.
pause
