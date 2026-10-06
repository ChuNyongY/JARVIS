@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "version=__VERSION__"
set "expectedPayloadHash=__PAYLOAD_SHA256__"
set "payload=%~dp0JAVIS-PC-payload.zip"

if not defined LOCALAPPDATA exit /b 10
if not exist "%payload%" exit /b 11

set "observedPayloadHash="
for /f "delims=" %%H in ('%SystemRoot%\System32\certutil.exe -hashfile "%payload%" SHA256 ^| %SystemRoot%\System32\findstr.exe /R /I "^[0-9A-F][0-9A-F]*$"') do if not defined observedPayloadHash set "observedPayloadHash=%%H"
if /I not "%observedPayloadHash%"=="%expectedPayloadHash%" exit /b 12

set "installParent=%LOCALAPPDATA%\JAVIS-PC"
set "destination=%installParent%\%version%"

if exist "%destination%\JAVIS.exe" (
  "%destination%\JAVIS.exe" verify-package "%destination%"
  if not errorlevel 1 (
    echo JAVIS-PC is already installed and verified.
    exit /b 0
  )
)
if exist "%destination%" exit /b 13

if not exist "%installParent%" mkdir "%installParent%"
if errorlevel 1 exit /b 14

set "staging=%installParent%\.installing-%RANDOM%%RANDOM%%RANDOM%"
if exist "%staging%" exit /b 15
mkdir "%staging%"
if errorlevel 1 exit /b 16

"%SystemRoot%\System32\tar.exe" -xf "%payload%" -C "%staging%"
if errorlevel 1 goto :fail_extract

if not exist "%staging%\JAVIS.exe" goto :fail_verify
"%staging%\JAVIS.exe" verify-package "%staging%"
if errorlevel 1 goto :fail_verify

move "%staging%" "%destination%" >nul
if errorlevel 1 goto :fail_move

echo Installed JAVIS-PC %version% to %destination%
echo Startup remains disabled. Provision the NIM credential separately.
exit /b 0

:fail_extract
set "failure=17"
goto :cleanup

:fail_verify
set "failure=18"
goto :cleanup

:fail_move
set "failure=19"

:cleanup
if exist "%staging%" rmdir /s /q "%staging%"
exit /b %failure%
