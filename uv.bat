@echo off
:: Copyright (c) 2024 The Chromium Authors. All rights reserved.
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

:: This wrapper shadows 'uv' to automatically maintain the hermetic environment.

setlocal

:: Get directory of this script
set "REPO_ROOT=%~dp0"
set "SETUP_SCRIPT=%REPO_ROOT%setup_uv.py"

:: Search for .vpython3 or .vpython up the directory tree
set "VPYTHON_SPEC="
set "SEARCH_DIR=%CD%"

:SearchLoop
if exist "%SEARCH_DIR%\.vpython3" (
    set "VPYTHON_SPEC=%SEARCH_DIR%\.vpython3"
    goto :Found
)
if exist "%SEARCH_DIR%\.vpython" (
    set "VPYTHON_SPEC=%SEARCH_DIR%\.vpython"
    goto :Found
)

:: Get parent directory
for %%I in ("%SEARCH_DIR%\..") do set "PARENT_DIR=%%~fI"

:: Check if we hit the root
if "%PARENT_DIR%"=="%SEARCH_DIR%" goto :NotFound
set "SEARCH_DIR=%PARENT_DIR%"
goto :SearchLoop

:Found
:: Run the setup script with the found spec file
python "%SETUP_SCRIPT%" "%VPYTHON_SPEC%"
if errorlevel 1 exit /b %errorlevel%

:NotFound

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
