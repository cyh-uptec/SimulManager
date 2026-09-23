# SimulManager (시뮬레이션 매니저)

EMS · RTDS · 시뮬매니저 3노드 HILS 환경에서 **ESS 규칙기반 운영(Run A) vs 기상 기반 최적 운영(Run B)** 의
피크저감·요금절감 효과를 비교 검증하기 위한 **시뮬레이션 매니저**입니다.

시뮬매니저는 Modbus TCP **서버 2개 링크**(링크① EMS용, 링크③ RTDS용)를 열고
가상시계 진행 · 기상/부하 시나리오 배포 · Heartbeat 생존감시 · Historian(SQLite) 적재 · 연결중개만 수행합니다.
ESS 운영 제어에는 개입하지 않습니다.

```
링크① EMS ──(Client)──▶ 시뮬매니저(Server) : 시각·기상·이벤트 읽기 / 예측·HB 기록
링크② EMS ──(Client)──▶ RTDS(Server)      : P/Q 지령 쓰기 / 계측·상태 읽기  (본 프로젝트 범위 외)
링크③ RTDS ─(Client)──▶ 시뮬매니저(Server) : 환경·시나리오 읽기 / HB·결과 기록
```

- 가상 30일, 15분 제어주기(96구간/일, 총 2,880구간), 1분 계측주기, 시간가속(예: 실 10 s = 가상 15분)
- 가상시계는 **자유진행** — 노드를 기다리지 않음. Heartbeat 실 1 s / 판정 3 s

## 구성

| 구분 | 기술 | 위치 |
|---|---|---|
| Backend | Python 3.11+ · FastAPI · pymodbus(async) · SQLite | `backend/` |
| Frontend | React · Vite · TypeScript (빌드 결과를 FastAPI가 정적 서빙) | `frontend/` |
| 에뮬레이터 | 단독 PC 검증용 간이 EMS · RTDS (Python) | `emulators/` |
| 문서 | 개발계획 · 프로토콜 규칙 · 미결 이슈 · 배포 · 시험 매뉴얼 | `docs/` |
| 원본 설계 | Modbus 프로토콜맵 v1.1(xlsx) · HILS 시퀀스 설계서 v1.7(docx) | 루트 |

`backend/app/protocol/`이 레지스터 맵의 **단일 소스(SSOT)** 입니다. 주소·스케일·부호는 코드 어디에도 하드코딩하지 않습니다.

## 요구 사항

- Windows 10/11, **Python 3.11 이상** (설치 시 "Add python.exe to PATH" 체크)
- **Node.js 18 이상** — 프론트엔드 빌드에만 필요 (저장소에는 빌드 결과 `frontend/dist/`가 포함되지 않음)

## 실행 방법

### 1. 저장소 받기

```bash
git clone https://github.com/cyh-uptec/SimulManager.git
cd SimulManager
```

### 2. 프론트엔드 빌드 (최초 1회, 이후 UI 수정 시)

```bash
cd frontend
npm install
npm run build
cd ..
```

### 3. 백엔드 설치·기동 (더블클릭)

| 파일 | 역할 |
|---|---|
| `install.bat` | 최초 1회. 루트에 `.venv` 생성 + backend 의존성 설치 |
| `run.bat` | 서버 기동(포트 8100) + 브라우저 자동 열기 |
| `run_emulators.bat` | 실장비 없이 시험할 때 EMS·RTDS 에뮬레이터 창 2개 실행 (run.bat 이후) |

기동 후 `http://localhost:8100` (다른 PC에서는 `http://<매니저IP>:8100`) 으로 접속합니다.

### 접속 포트 (기본 설정 `backend/config/default.yaml`)

| 대상 | 포트 | 비고 |
|---|---|---|
| 운영자 웹 UI · REST · WebSocket | 8100 | |
| 링크① Modbus 서버 (EMS 접속) | 7000 | 시험 모드 `per_link_port` |
| 링크③ Modbus 서버 (RTDS 접속) | 5021 | 시험 모드 `per_link_port` |

운영 배포(단일 포트 502, 실 IP)는 `production.yaml`을 쓰며, 방법은 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)를 참조합니다.
UI 설정(⚙)에서 저장한 값은 `backend/config/user_settings.yaml`에 기록되며 PC별 파일이라 저장소에 포함되지 않습니다.

## 개발

```bash
# Backend (backend/) — 자동 리로드
..\.venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8100
pytest

# Frontend (frontend/) — 개발 서버
npm run dev
```

## 문서

| 문서 | 내용 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 프로젝트 개요 · 핵심 규칙 요약 · 개발 단계 현황 |
| [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) | Phase 1~7 개발계획 · DoD · 검증 전략 |
| [docs/PROTOCOL_RULES.md](docs/PROTOCOL_RULES.md) | 레지스터 표기 · 스케일/부호 · 핸드셰이크 · 예외 · Run A/B 조건 |
| [docs/OPEN_ISSUES.md](docs/OPEN_ISSUES.md) | 협의 필요 항목과 잠정 대응 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) · [docs/배포.md](docs/배포.md) | 다른 PC 배포 방법 · 복사 대상 |
| [docs/EMS_시험_매뉴얼.md](docs/EMS_시험_매뉴얼.md) | EMS 시험 페이지 사용 매뉴얼 |

## 개발 현황

- [x] Phase 1 기반 골격 · Phase 2 Modbus 서버 · Phase 3 시나리오·시퀀스 엔진
- [x] Phase 4 EMS/RTDS 에뮬레이터 · Phase 5 Historian·실시간 대시보드
- [ ] Phase 6 Run 관리·평가·리포트
- [ ] Phase 7 예외·안정화·배포

> DevExpress WPF 포팅판은 상용 라이브러리 의존으로 이 저장소에 포함하지 않습니다 (구조 대응표: [docs/WPF_PORT.md](docs/WPF_PORT.md)).
