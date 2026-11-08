@echo off
rem Launcher for the Remi UI sketch panels.
rem   run-ui-sketch.bat              -> Generate workspace variants (Blender 5.3)
rem   run-ui-sketch.bat ui_sketch.py -> session-shell variants
setlocal

set "SKETCH=%~dp0ui_generate_sketch.py"
if not "%~1"=="" set "SKETCH=%~dp0%~1"

set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe"
if not exist "%BLENDER%" set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.3\blender.exe"
if not exist "%BLENDER%" set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
if not exist "%BLENDER%" set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

if not exist "%BLENDER%" (
  echo.
  echo Blender was not found in "C:\Program Files\Blender Foundation".
  echo Edit this file and set BLENDER to your blender.exe path.
  echo.
  pause
  exit /b 1
)

if not exist "%SKETCH%" (
  echo.
  echo Missing sketch file: "%SKETCH%"
  echo.
  pause
  exit /b 1
)

echo Launching:
echo   %BLENDER%
echo   %SKETCH%
echo.
echo In Blender: press N in the viewport, then open the "GEN" tab at the
echo bottom of the sidebar strip. The add-on's own panels stay in "Remi".
echo.

"%BLENDER%" --factory-startup --python "%SKETCH%"

if errorlevel 1 (
  echo.
  echo Blender reported an error. Copy the text above when asking for help.
  pause
)
endlocal
