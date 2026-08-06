@echo off
:: Copyright 2022 The Chromium Authors
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.
setlocal

:: Prepend python-bin to PATH so subprocesses use CIPD Python, and ensure
:: depot_tools is on PATH for standalone usage.
set PATH=%~dp0python-bin;%PATH%;%~dp0

:: Defer control.
call "%~dp0python-bin\python3.bat" "%~dp0\ninja.py" "%*"
