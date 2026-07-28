@echo off
:: Copyright 2026 The Chromium Authors
:: Use of this source code is governed by a BSD-style license that can be
:: found in the LICENSE file.

call "%~dp0python-bin\python3.bat" %*
exit /b %ERRORLEVEL%
