@echo off
cd /d "%~dp0"

echo ===== mapaopao start =====

REM backend: FastAPI / uvicorn on port 8000
netstat -ano | findstr /R ":8000 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [backend] port 8000 already running, skip
) else (
  echo [backend] starting uvicorn ...
  start "mapaopao-api" cmd /k ".venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000"
)

REM frontend: Vite dev server on port 7100
netstat -ano | findstr /R ":7100 .*LISTENING" >nul
if %errorlevel%==0 (
  echo [frontend] port 7100 already running, skip
) else (
  echo [frontend] starting vite ...
  start "mapaopao-web" cmd /k "cd /d %~dp0app && npm run dev"
)

ping -n 5 127.0.0.1 >nul
start http://localhost:7100/
echo.
echo done: backend http://127.0.0.1:8000  frontend http://localhost:7100
echo run stop.bat to shut down
ping -n 7 127.0.0.1 >nul
