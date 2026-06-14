@echo off
echo Stopping all hidden S2 background processes...

:: Kill only the python process running run_bot.py
wmic process where "name='python.exe' and commandline like '%%run_bot.py%%'" call terminate >nul 2>&1

echo.
echo S2 is now offline!
timeout /t 3 >nul
