@echo off
rem Launcher for the Remi UI sketches.
rem   run-ui-sketch.bat              -> Docked Remi workspace (Blender 5.3)
rem   run-ui-sketch.bat ui_nodes_sketch.py -> only the node graph
setlocal

set "SKETCH_DIR=%~dp0"

set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe"
if not exist "%BLENDER%" set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"

if not exist "%BLENDER%" (
  echo.
  echo Blender 5.3 was not found in "C:\Program Files\Blender Foundation".
  echo Edit this file and set BLENDER to your blender.exe path.
  echo.
  pause
  exit /b 1
)

set "SKETCH=ui_board_sketch.py"
if not "%~1"=="" set "SKETCH=%~1"
if not exist "%SKETCH_DIR%%SKETCH%" (
  echo.
  echo Missing sketch file: "%SKETCH_DIR%%SKETCH%"
  echo.
  pause
  exit /b 1
)

rem --factory-startup resets Tablet API to Automatic, which can wait ~36s on
rem a stuck Wacom/WinTab driver. Prepare a separate clean Windows Ink profile
rem headlessly, then load it normally. Never change the customer's preferences.
set "REMI_BLENDER_VERSION="
for /f "tokens=1,2" %%V in ('""%BLENDER%" --version"') do if "%%V"=="Blender" set "REMI_BLENDER_VERSION=%%W"
if not defined REMI_BLENDER_VERSION (
  echo Could not detect the Blender version.
  pause
  exit /b 1
)
set "BLENDER_USER_CONFIG=%LOCALAPPDATA%\Remi\ui-sketch\%REMI_BLENDER_VERSION%\config"
"%BLENDER%" --background --factory-startup --python-exit-code 1 --python "%SKETCH_DIR%ui_sketch_profile.py"
if errorlevel 1 (
  echo Could not prepare the isolated UI sketch profile.
  pause
  exit /b 1
)

echo Launching %SKETCH% in the isolated Windows Ink profile
echo.
"%BLENDER%" --python "%SKETCH_DIR%%SKETCH%"

if errorlevel 1 (
  echo.
  echo Blender reported an error. Copy the text above when asking for help.
  pause
)
endlocal
