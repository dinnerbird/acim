@echo off
rem CoGS Image Manager, text edition: Windows launcher.
rem Double-click, or: cim-text.bat "C:\Apple II Disks"
rem Needs Python 3 from python.org or the Microsoft Store. Windows Python has
rem no curses, so cim.py runs in its plain command mode (type ? for commands).
rem For the full-screen view:  pip install windows-curses
setlocal
set DIR=%~dp0

set PY=
py -3 -c "pass" >nul 2>&1 && set PY=py -3
if "%PY%"=="" python -c "pass" >nul 2>&1 && set PY=python
if "%PY%"=="" (
    echo No Python found. Install Python 3 from python.org or the Microsoft Store,
    echo tick "Add python.exe to PATH" in the installer, then run this again.
    pause
    exit /b 1
)

set LIB=%~1
if not "%LIB%"=="" if exist "%LIB%\" goto run
if exist "%DIR%Disk Images\" (
    set LIB=%DIR%Disk Images
    goto run
)
if exist "%USERPROFILE%\.cim-text-library" (
    set /p LIB=<"%USERPROFILE%\.cim-text-library"
    if exist "%LIB%\" goto run
)
:ask
set /p LIB=Folder of disk images to share: 
if not exist "%LIB%\" (
    echo Not a folder: %LIB%
    goto ask
)

:run
<nul set /p =%LIB%>"%USERPROFILE%\.cim-text-library"
%PY% "%DIR%cim.py" "%LIB%" %2 %3 %4 %5 %6 %7 %8 %9
pause
