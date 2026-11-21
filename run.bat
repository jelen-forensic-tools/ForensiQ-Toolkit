@echo off
rem Run ForenziQ from source.
rem Uses the Python launcher (py) if available, otherwise python from PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0main.py"
) else (
    python "%~dp0main.py"
)
