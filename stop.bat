@echo off
echo ===== mapaopao stop =====

for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R ":7100 .*LISTENING"') do (
  echo killing frontend PID %%a
  taskkill /PID %%a /F /T >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R ":8000 .*LISTENING"') do (
  echo killing backend PID %%a
  taskkill /PID %%a /F /T >nul 2>&1
)

echo.
echo stopped: backend 8000 + frontend 7100
ping -n 5 127.0.0.1 >nul
