@echo off
cd /d "%~dp0"
title SimulManager 로고 적용
echo ============================================
echo  로고 적용: ICO 생성 + 웹 로고 + 바탕화면 바로가기
echo ============================================

if not exist .venv\Scripts\python.exe (
    echo [오류] 먼저 install.bat 을 실행하세요.
    pause
    exit /b 1
)
if not exist assets\logo.png (
    echo [오류] assets\logo.png 가 없습니다.
    echo 회사 로고를 assets\logo.png 로 저장한 뒤 다시 실행하세요. (권장 512x512 이상 PNG)
    pause
    exit /b 1
)

echo [1/3] ICO, favicon, 웹 로고 생성...
".venv\Scripts\python.exe" scripts\make_icon.py
if errorlevel 1 ( pause & exit /b 1 )

echo [2/3] frontend 재빌드 (웹 로고 반영)...
where npm >nul 2>nul
if errorlevel 1 (
    echo   npm 이 없어 재빌드 생략. 개발 PC에서 npm run build 후 dist 복사 필요
) else (
    pushd frontend
    call npm run build
    popd
)

echo [3/3] 바탕화면 바로가기 생성...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\SimulManager.lnk'); $lnk.TargetPath = '%~dp0run.bat'; $lnk.WorkingDirectory = '%~dp0'; $lnk.IconLocation = '%~dp0assets\app.ico,0'; $lnk.Description = 'SimulManager - HILS 시뮬레이션 매니저'; $lnk.Save()"
if errorlevel 1 ( echo [오류] 바로가기 생성 실패 & pause & exit /b 1 )

echo.
echo 완료! 바탕화면의 SimulManager 바로가기로 실행하세요. (아이콘 반영됨)
echo 웹 로고는 서버 재시작 후 브라우저 새로고침(Ctrl+F5) 시 반영됩니다.
pause
