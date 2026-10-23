@echo off
rem Starts Model Librarian Gallery in its own window.
rem The first start makes its own Python environment in .venv (needs uv: https://docs.astral.sh/uv/).
rem   run.bat --browser   opens it in your web browser instead
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  where uv >nul 2>nul || (
    echo The first start needs uv to set up Python: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
  )
  echo Setting up the Gallery's own Python, once...
  uv venv --python 3.12 .venv || goto :fail
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt || goto :fail
)
if /i "%~1"=="--browser" (
  ".venv\Scripts\python.exe" -m mlgallery %*
) else (
  start "" ".venv\Scripts\pythonw.exe" -m mlgallery %*
)
exit /b 0
:fail
echo Setup didn't finish: see the messages above.
pause
exit /b 1
