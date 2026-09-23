@echo off
cd /d "%~dp0"
title SimulManager 에뮬레이터 (단독 PC 테스트)
echo ============================================
echo  간이 EMS / RTDS 에뮬레이터 실행 (단독 PC 테스트)
echo  전제: run.bat 으로 매니저가 이미 실행 중이어야 함
echo ============================================

if not exist .venv\Scripts\python.exe (
    echo [오류] 먼저 install.bat 을 실행하세요.
    pause
    exit /b 1
)

echo RTDS 에뮬레이터 창 실행...
start "RTDS Emulator" cmd /k ".venv\Scripts\python.exe emulators\rtds_emulator.py"
timeout /t 2 >nul
echo EMS 에뮬레이터 창 실행...
start "EMS Emulator" cmd /k ".venv\Scripts\python.exe emulators\ems_emulator.py"

echo.
echo 두 에뮬레이터 창이 열렸습니다. 대시보드에서 EMS/RTDS 노드가 '정상'으로
echo 바뀌면 [Run 시작] 을 눌러 시퀀스를 확인하세요. (종료: 각 창 닫기)
pause
