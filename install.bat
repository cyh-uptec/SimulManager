@echo off
cd /d "%~dp0"
title SimulManager 설치
echo ============================================
echo  SimulManager 설치 (최초 1회)
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.11 이상을 설치하세요.
    echo 설치 시 "Add python.exe to PATH" 체크 필수.
    pause
    exit /b 1
)

if not exist .venv (
    echo [1/2] 가상환경 생성 중...
    python -m venv .venv
)

echo [2/2] 의존성 설치 중...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip --quiet
if exist wheels (
    echo   - 오프라인 모드: wheels 폴더에서 설치
    pip install --no-index --find-links wheels -e ./backend
) else (
    pip install -e ./backend
)
if errorlevel 1 (
    echo [오류] 설치에 실패했습니다. 위 메시지를 확인하세요.
    pause
    exit /b 1
)

echo.
echo 설치 완료. run.bat 을 더블클릭하여 실행하세요.
pause
