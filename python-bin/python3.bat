@echo off
:: Copyright 2021 The Chromium Authors
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

if defined VPYTHON_BYPASS if not "%VPYTHON_BYPASS%"=="0" goto :BYPASS
if defined DEPOT_TOOLS_PYTHON_BYPASS if not "%DEPOT_TOOLS_PYTHON_BYPASS%"=="0" goto :BYPASS

setlocal

for %%d in (%~dp0..) do set PARENT_DIR=%%~fd
IF NOT EXIST "%PARENT_DIR%\python3_bin_reldir.txt" (
  echo python3_bin_reldir.txt not found. need to initialize depot_tools by >&2
  echo running gclient or update_depot_tools >&2
  exit /b 1
)

:Initialized
for /f %%i in (%PARENT_DIR%\python3_bin_reldir.txt) do set PYTHON_BIN_ABSDIR=%PARENT_DIR%\%%i
set PATH=%PYTHON_BIN_ABSDIR%;%PYTHON_BIN_ABSDIR%\Scripts;%PATH%
"%PYTHON_BIN_ABSDIR%\python3.exe" %*
exit /b %ERRORLEVEL%

:BYPASS
for %%i in (python3.exe) do if not "%%~$PATH:i"=="" (
  "%%~$PATH:i" %*
  exit /b %ERRORLEVEL%
)
for %%i in (python.exe) do if not "%%~$PATH:i"=="" (
  "%%~$PATH:i" %*
  exit /b %ERRORLEVEL%
)
echo depot_tools: Error: System python3/python not found in PATH (bypass enabled). >&2
exit /b 1
