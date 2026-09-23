# 배포 가이드 — 시뮬매니저 운영 PC 설치

> 대상: 시뮬매니저 운영 PC (예: 192.168.0.10, Windows).
> 원칙: **모든 실행은 더블클릭(bat 파일)** 으로 가능해야 한다. frontend는 빌드 산출물을
> FastAPI가 정적 서빙하므로 운영 PC에 Node.js 불필요, SQLite라서 DB 서버 설치도 없음.

## 1. 배포 폴더 구조 (운영 PC)

```
C:\SimulManager\
├── install.bat            # 최초 1회: venv 생성 + 의존성 설치 (더블클릭)
├── run.bat                # 실행: 서버 기동 + 브라우저 자동 열기 (더블클릭)
├── backend\
│   ├── app\               # Python 소스
│   ├── config\
│   │   ├── default.yaml       # 개발 기본값 (포트 분리, 127.0.0.1)
│   │   └── production.yaml    # 운영 설정 (single_port 502, 실 IP) ★배포 시 사용
│   └── pyproject.toml
├── frontend\dist\         # npm run build 산출물 (개발 PC에서 빌드해서 복사)
└── data\                  # historian.db·시나리오 CSV — 업데이트 시 보존 대상
```

## 2. 접속 지점

| 대상 | 주소 | 비고 |
|---|---|---|
| EMS / RTDS (Modbus TCP) | `192.168.0.10:502` | production.yaml `mode: single_port` |
| 운영자 웹 UI | `http://192.168.0.10:8100` | 아무 PC 브라우저에서 접속 (API·WebSocket·UI 단일 포트) |

## 3. 배포 방식

### 방식 A — Python 설치 + 소스 배포 (개발 중간 배포 권장)
1. 운영 PC에 Python 3.11+ 설치 (1회, 설치 시 "Add python.exe to PATH" 체크)
2. 개발 PC에서 frontend 빌드: `frontend/`에서 `npm run build` → `frontend/dist/` 생성
3. 복사 대상만 `C:\SimulManager\`로 복사 — **필수/선택/제외 목록은 [배포.md](배포.md) 참조**
   (핵심: `.venv`·`node_modules`·WPF `SimulManager\` 제외, frontend는 `dist\`만)
4. **`install.bat` 더블클릭** → venv 생성 + 의존성 설치
   - 오프라인 PC: 개발 PC에서 `pip download -d wheels -r <요구사항>`으로 wheel을 받아
     함께 복사하면 install.bat이 `wheels\` 폴더를 자동 인식해 오프라인 설치
5. **`run.bat` 더블클릭** → 서버 기동 + 브라우저 자동 열림

### 방식 B — PyInstaller 패키징 (최종 운영, Phase 7)
- 개발 PC에서 onedir 빌드 → `SimulManager.exe` 포함 폴더만 복사, Python 설치 불필요
- pymodbus 등 hidden import 검증 필요. Phase 7에서 빌드 스크립트 제공 예정

## 4. 운영 PC 설정 체크리스트 (방식 무관)

- [ ] **고정 IP** 설정: 192.168.0.10 (EMS·RTDS가 이 주소로 접속)
- [ ] **방화벽 인바운드 허용**: TCP 502(Modbus), TCP 8100(웹 UI)
  ```
  netsh advfirewall firewall add rule name="SimulManager Modbus" dir=in action=allow protocol=TCP localport=502
  netsh advfirewall firewall add rule name="SimulManager Web" dir=in action=allow protocol=TCP localport=8100
  ```
- [ ] **운영 설정 적용**: `run.bat`은 `SIMUL_CONFIG` 환경변수 또는 인자로 설정 파일 선택.
      운영 PC에서는 `production.yaml`(single_port 502, 실 EMS/RTDS IP) 사용.
      **주의**: UI(⚙ 설정→네트워크)로 저장한 `config\user_settings.yaml`이 production.yaml보다
      **우선** 적용된다 — 시험 PC에서 복사해 온 user_settings.yaml에 per_link_port가 남아 있으면
      운영에서도 포트 분리로 뜨므로, 운영 PC 최초 기동 후 UI에서 single_port·실 IP로 저장(또는
      user_settings.yaml의 network 섹션 삭제)할 것
- [ ] **자동 시작(선택)**: 작업 스케줄러(로그온 시 `run.bat`) 또는 NSSM 서비스 등록
- [ ] **로고·아이콘(선택)**: 회사 로고를 `assets\logo.png`(512px+ PNG)로 넣고 `apply_logo.bat` 더블클릭
      → 멀티사이즈 ICO(16~256px)·웹 파비콘·사이드바 로고 생성 + **바탕화면 바로가기**(run.bat, 아이콘 지정) 생성.
      bat 파일은 아이콘을 가질 수 없어 바로가기(.lnk) 방식 사용, Phase 7 exe 패키징 시 ico 직접 내장 (assets/README.md 참조)
- [ ] 에뮬레이터는 배포 제외가 기본 (실장비 없이 현장 점검 시에만 옵션 포함)

## 5. 업데이트·백업

- **업데이트**: `data\`와 `backend\config\production.yaml`을 보존하고 코드·`frontend\dist\`만
  교체 → run.bat 재실행. (install.bat은 의존성 변경 시에만 재실행)
- **백업**: `data\historian.db` 파일 복사 = 전체 Run 이력 백업 (SQLite 단일 파일)

## 6. 문제 해결

| 증상 | 확인 |
|---|---|
| run.bat 창이 바로 닫힘 | install.bat을 먼저 실행했는지, 창의 오류 메시지 확인(pause로 유지됨) |
| EMS/RTDS 접속 불가 | 방화벽 502 허용, production.yaml의 `mode`/IP, `netstat -ano \| findstr :502` |
| 웹 UI 접속 불가 | 방화벽 8100 허용, 서버 콘솔 로그, `frontend\dist` 존재 여부 |
| 다른 PC에서 UI 안 열림 | bind가 0.0.0.0인지 (config `network.bind`) |
