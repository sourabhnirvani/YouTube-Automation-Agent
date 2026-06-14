@echo off
cd /d "%~dp0"
echo Starting S2 in the hidden background...

:: Create a temporary script to launch python invisibly without keeping a console open
echo Set objShell = CreateObject("WScript.Shell") > launch_hidden.vbs
echo objShell.Run "cmd /c python run_bot.py", 0, False >> launch_hidden.vbs

:: Run the script
cscript //nologo launch_hidden.vbs

:: Clean up the temporary script
del launch_hidden.vbs

echo.
echo S2 is now running invisibly!
echo You can safely close this IDE and she will stay online.
timeout /t 3 >nul
