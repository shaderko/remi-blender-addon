@echo off
rem Launcher for the Remi UI sketches.
rem   run-ui-sketch.bat              -> Generate layouts + Image graph (Blender 5.3)
rem   run-ui-sketch.bat ui_nodes_sketch.py -> only the node graph
setlocal

set "SKETCH_DIR=%~dp0"

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

if not "%~1"=="" (
  if not exist "%SKETCH_DIR%%~1" (
    echo.
    echo Missing sketch file: "%SKETCH_DIR%%~1"
    echo.
    pause
    exit /b 1
  )
  echo Launching %~1
  echo.
  "%BLENDER%" --factory-startup --python "%SKETCH_DIR%%~1"
) else (
  echo Launching Generate layouts + Image board
  echo.
  "%BLENDER%" --factory-startup --python "%SKETCH_DIR%ui_generate_sketch.py" --python "%SKETCH_DIR%ui_board_sketch.py"
)

if errorlevel 1 (
  echo.
  echo Blender reported an error. Copy the text above when asking for help.
  pause
)
endlocal
