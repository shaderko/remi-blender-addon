@echo off
rem Double-click launcher for the UI shell sketches.
rem Registers the sketch panels in the N-panel "UI" tab. Close Blender to end it.
setlocal

set "SKETCH=%~dp0ui_sketch.py"

set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
if not exist "%BLENDER%" set "BLENDER=C:\Program Files\Blender Foundation\Blender 5.3 Beta\blender.exe"
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

echo Launching Blender with the Remi UI sketches...
echo   %BLENDER%
echo   %SKETCH%
echo.
echo In Blender: press N in the viewport, then pick the "UI" tab at the
echo bottom of the sidebar strip and expand "Sketch Control".
echo.

"%BLENDER%" --factory-startup --python "%SKETCH%"

if errorlevel 1 (
  echo.
  echo Blender reported an error. Copy the text above when asking for help.
  pause
)
endlocal
