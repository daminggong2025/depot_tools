@echo off
:: Copyright (c) 2024 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

:: This wrapper shadows 'uv' to automatically maintain the hermetic environment.

setlocal

:: Get directory of this script
set "REPO_ROOT=%~dp0"
set "SETUP_SCRIPT=%REPO_ROOT%setup_uv.py"

:: Run the setup script (fast-exits if up to date)
python "%SETUP_SCRIPT%"
if errorlevel 1 exit /b %errorlevel%

:: Find the real uv.exe
:: We use 'where' but we need to skip the one in current dir if it's in PATH first.
:: Actually, Windows PATH lookup will find this .bat (or .cmd) first?
:: Wait, if user types 'uv', cmd looks for uv.com, uv.exe, uv.bat, uv.cmd.
:: If we name this uv.bat, and real uv is uv.exe...
:: If they are in the SAME directory, precedence depends on PATHEXT.
:: Usually .COM .EXE .BAT .CMD
:: So uv.exe (real) would beat uv.bat (wrapper) if in same dir!
:: But depot_tools is usually prepended to PATH.
:: Real uv is likely in ~/.cargo/bin or similar.

:: We want to find 'uv.exe' that is NOT in REPO_ROOT.
set "REAL_UV="
for /f "tokens=*" %%i in ('where uv.exe 2^>nul') do (
    if /i not "%%~dpi"=="%REPO_ROOT%" (
        set "REAL_UV=%%i"
        goto :Found
    )
)

:Found
if "%REAL_UV%"=="" (
    echo Error: Could not find system 'uv.exe' in PATH.
    exit /b 1
)

endlocal & "%REAL_UV%" %*
