@echo off
cd /d "%~dp0"
title SimulManager 서버
echo ============================================
echo  SimulManager 실행 - http://localhost:8100
echo  (종료: 이 창에서 Ctrl+C 또는 창 닫기)
echo ============================================

if not exist .venv\Scripts\python.exe (
    echo [오류] 먼저 install.bat 을 실행하세요.
    pause
    exit /b 1
)

rem 운영 PC 배포 시 아래 줄 주석(rem) 해제 - production.yaml 사용 (docs/DEPLOYMENT.md)
rem set SIMUL_CONFIG=config\production.yaml

rem 서버 기동 2초 후 브라우저 자동 열기
start "" /b cmd /c "timeout /t 2 >nul & start http://localhost:8100"

cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8100
pause
