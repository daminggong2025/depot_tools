@echo off
:: Copyright 2026 The Chromium Authors
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

setlocal
set "ROOT_DIR=%~dp0."
for %%d in ("%ROOT_DIR%") do set "ROOT_DIR=%%~fd"

if defined VPYTHON_BYPASS if not "%VPYTHON_BYPASS%"=="" if not "%VPYTHON_BYPASS%"=="0" goto :BYPASS
if defined DEPOT_TOOLS_PYTHON_BYPASS if not "%DEPOT_TOOLS_PYTHON_BYPASS%"=="" if not "%DEPOT_TOOLS_PYTHON_BYPASS%"=="0" goto :BYPASS

call "%~dp0python-bin\python3.bat" %*
exit /b %ERRORLEVEL%

:BYPASS
:: Note: Filter out executables located under %ROOT_DIR% by combining
:: where.exe and findstr.exe so CIPD python directories are ignored.
for /f "delims=" %%i in ('where.exe python3.exe python.exe 2^>nul ^| findstr.exe /i /v /c:"%ROOT_DIR%"') do (
  set "SYS_PYTHON=%%~fi"
  goto :RUN_SYS_PYTHON
)
echo depot_tools: Error: System python3/python not found in PATH (bypass enabled). >&2
exit /b 1

:RUN_SYS_PYTHON
"%SYS_PYTHON%" %*
exit /b %ERRORLEVEL%
