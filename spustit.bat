@echo off
rem Spusti ForenziQ ze zdrojovych kodu / Run ForenziQ from source.
rem Pouzije Python launcher (py), pripadne python z PATH.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0main.py"
) else (
    python "%~dp0main.py"
)
