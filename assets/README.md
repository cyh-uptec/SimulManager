# assets/ — 로고·아이콘

## 회사 로고 적용 방법

1. 로고 이미지 저장:
   - **`assets/logo.png`** — 마크(심볼)만 있는 정사각 이미지 → 실행 아이콘·사이드바·파비콘 (권장 512×512 이상, 최소 102×102, 투명배경 PNG)
   - **`assets/logo_wide.png`** — 회사명 포함 와이드 로고 → 메인 화면(운전 현황) 상단 표시 (선택)
2. 프로젝트 루트의 **`apply_logo.bat` 더블클릭**

자동으로 수행되는 작업:
- `assets/app.ico` 생성 — 멀티사이즈 ICO (16·24·32·48·64·**102**·128·256px)
- `frontend/public/` 에 favicon.ico·logo.png·logo_wide.png 생성 → 웹 대시보드 반영 (frontend 재빌드 포함)
- **바탕화면에 `SimulManager.lnk` 바로가기** 생성 — run.bat 실행 + app.ico 아이콘

> bat 파일 자체는 Windows 구조상 아이콘을 가질 수 없어 바로가기(.lnk)에 아이콘을 지정한다.
> Phase 7의 PyInstaller exe 패키징 시에는 exe에 app.ico가 직접 내장된다.

현재 `logo.png`는 임시 플레이스홀더 — 회사 로고로 교체 후 apply_logo.bat 재실행.
