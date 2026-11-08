@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Yunlan v1.1.0 one-click installer.
rem The same file is published as install-yunlan-github.cmd and
rem install-yunlan-gitee.cmd. The file name selects the download source.

set "VERSION=1.1.0"
set "EXPECTED=4B85E4AC87744ED424947ACE15716D52C0D14CE8699CE18632EBCA43237E76B0"
set "TARGET=%USERPROFILE%\.dsh\plugins\dsh-dafeiyu-growth"
if defined YUNLAN_TARGET set "TARGET=%YUNLAN_TARGET%"
set "SOURCE=github"
if /I "%~n0"=="install-yunlan-gitee" set "SOURCE=gitee"
if defined YUNLAN_SOURCE set "SOURCE=%YUNLAN_SOURCE%"
set "WORK=%TEMP%\yunlan-install-%RANDOM%%RANDOM%"
set "ZIP=%WORK%\dsh-dafeiyu-growth-v1.1.0-windows-x64.zip"
set "STAGE=%WORK%\stage"

echo.
echo Yunlan %VERSION% installer
echo Source: %SOURCE%
echo Target: %TARGET%
echo.

if not defined YUNLAN_ALLOW_RUNNING (
    tasklist /FO CSV /NH 2>nul | findstr /I "dsh-dafeiyu-helper" >nul
    if not errorlevel 1 (
        echo Yunlan is still running. Please exit DeepSeek Harness completely, then run this installer again.
        goto :fail
    )
)

if exist "%TARGET%\package.json" (
    echo Existing Yunlan installation detected.
    echo Local data files will not be deleted; package files will be updated.
) else (
    echo New installation.
)
echo.

if not exist "%WORK%" mkdir "%WORK%" >nul 2>&1
if not exist "%WORK%" (
    echo Could not create temporary directory: %WORK%
    goto :fail
)

if defined YUNLAN_LOCAL_ZIP (
    set "ZIP=%YUNLAN_LOCAL_ZIP%"
    echo Using local package: %ZIP%
    goto :verify
)

if /I "%SOURCE%"=="gitee" goto :download_gitee

:download_github
echo Downloading GitHub package...
curl.exe --location --fail --retry 3 --retry-delay 2 --output "%ZIP%" "https://github.com/shjsxx/dsh-dafeiyu-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip"
if errorlevel 1 (
    echo GitHub download failed. Trying the Gitee mirror...
    goto :download_gitee
)
goto :verify

:download_gitee
echo Downloading Gitee package in four parts...
curl.exe --location --fail --retry 3 --retry-delay 2 --output "%ZIP%.001" "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.001"
if errorlevel 1 goto :download_failed
curl.exe --location --fail --retry 3 --retry-delay 2 --output "%ZIP%.002" "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.002"
if errorlevel 1 goto :download_failed
curl.exe --location --fail --retry 3 --retry-delay 2 --output "%ZIP%.003" "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.003"
if errorlevel 1 goto :download_failed
curl.exe --location --fail --retry 3 --retry-delay 2 --output "%ZIP%.004" "https://gitee.com/gyk2016/dafeiyu-ai-growth/releases/download/v1.1.0/dsh-dafeiyu-growth-v1.1.0-windows-x64.zip.004"
if errorlevel 1 goto :download_failed
echo Joining parts...
copy /b "%ZIP%.001" + "%ZIP%.002" + "%ZIP%.003" + "%ZIP%.004" "%ZIP%" >nul
if errorlevel 1 (
    echo Could not join the downloaded parts.
    goto :fail
)
goto :verify

:verify
if not exist "%ZIP%" (
    echo Package was not found: %ZIP%
    goto :fail
)
echo Verifying SHA-256...
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%ZIP%" SHA256') do (
    set "HASH=%%H"
    goto :hash_got
)
:hash_got
set "HASH=%HASH: =%"
if /I not "%HASH%"=="%EXPECTED%" (
    echo SHA-256 mismatch.
    echo Expected: %EXPECTED%
    echo Actual:   %HASH%
    goto :fail
)
echo SHA-256 OK.

echo Extracting...
if not exist "%STAGE%" mkdir "%STAGE%" >nul 2>&1
where tar.exe >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%STAGE%' -Force"
) else (
    tar.exe -xf "%ZIP%" -C "%STAGE%"
)
if errorlevel 1 (
    echo Extraction failed.
    goto :fail
)
if not exist "%STAGE%\package.json" (
    echo Extracted package is incomplete: package.json is missing.
    goto :fail
)

if not exist "%TARGET%" mkdir "%TARGET%" >nul 2>&1
echo Installing into plugin directory...
xcopy "%STAGE%\*" "%TARGET%\" /E /I /Y >nul
if errorlevel 2 (
    echo Could not copy the package into the plugin directory.
    goto :fail
)
if not exist "%TARGET%\package.json" (
    echo Installation check failed: package.json is missing in the target.
    goto :fail
)

echo.
echo Installation complete.
echo Restart DeepSeek Harness, then enable Yunlan in Settings - Plugins.
echo Your existing local data files were not deleted.
if not defined YUNLAN_NO_OPEN start "" "%TARGET%"
if not defined YUNLAN_NO_PAUSE pause
rmdir /s /q "%WORK%" >nul 2>&1
exit /b 0

:download_failed
echo Download failed. Check your network and try again.
goto :fail

:fail
echo.
echo Installation did not complete.
if not defined YUNLAN_NO_PAUSE pause
exit /b 1
