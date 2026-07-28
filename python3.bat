@echo off
if defined VPYTHON_BYPASS if not "%VPYTHON_BYPASS%"=="0" goto :BYPASS
if defined DEPOT_TOOLS_PYTHON_BYPASS if not "%DEPOT_TOOLS_PYTHON_BYPASS%"=="0" goto :BYPASS

call %~dp0python-bin\python3.bat %*
exit /b %ERRORLEVEL%

:BYPASS
python.exe %*
exit /b %ERRORLEVEL%
