@echo off
rem Run ForenziQ from source.
rem Uses the first Python that has the ForenziQ requirements installed
rem (python on PATH, then the py launcher versions), so a newer Python
rem without the packages does not get picked by mistake.
setlocal
set "MAIN=%~dp0main.py"

call :try python && goto :eof
call :try py -3.13 && goto :eof
call :try py -3.12 && goto :eof
call :try py -3.11 && goto :eof
call :try py -3 && goto :eof

echo.
echo No Python with the ForenziQ requirements was found.
echo Install them with:  pip install -r requirements.txt
echo (Python 3.11 or newer is required.)
pause
exit /b 1

:try
%* -c "import customtkinter" >nul 2>nul || exit /b 1
%* "%MAIN%"
if errorlevel 1 pause
exit /b 0
