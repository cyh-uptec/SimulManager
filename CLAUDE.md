# SimulManager (시뮬레이션 매니저)

EMS · RTDS · 시뮬매니저 3노드 HILS 환경에서 **ESS 규칙기반 운영(Run A) vs 기상 기반 최적
운영(Run B)** 의 피크저감·요금절감 효과를 비교 검증하는 **시뮬레이션 매니저**.
Backend/Frontend 구조로 Phase 1~7 단계 개발한다. 각 노드는 별도 PC에서 운영된다.

## 문서 체계와 유지관리 규칙 (중요)

| 문서 | 내용 |
|---|---|
| `EMS-RTDS_시뮬매니저_Modbus_프로토콜맵_v1.1.xlsx` | 원본 설계: 3개 링크 레지스터 맵·역할·타이밍 (v1.1: 링크① 미러 HR 열·IR [2~4구간] 추가) |
| `EMS-RTDS-시뮬매니저_HILS_시뮬레이션_시퀀스_설계서_v1.7.docx` | 원본 설계: P0~P7 시퀀스·예외·평가지표 (v1.6: HR 미러 확정 / v1.7: 예측 블록 [1구간]만 전달) |
| [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) | 단계별 개발계획서 — 아키텍처, Phase 1~7 상세·DoD, 검증 전략 |
| [docs/PROTOCOL_RULES.md](docs/PROTOCOL_RULES.md) | 프로토콜·도메인 핵심 규칙 — 레지스터 표기, 스케일·부호, 원자성·핸드셰이크, 자유진행 원칙, 예외, Run A/B 조건, 설비 정수 |
| [docs/OPEN_ISSUES.md](docs/OPEN_ISSUES.md) | 미결정(협의 필요) 항목과 잠정 대응 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 다른 PC 배포 방법 — 폴더 구조, install/run.bat, 방화벽·IP 체크리스트 |
| [docs/배포.md](docs/배포.md) | 배포 시 복사 대상 정리 — 필수/선택/제외 목록, user_settings.yaml 시험·운영 구분 |
| [docs/PROJECT_CHECKLIST.md](docs/PROJECT_CHECKLIST.md) | 장치별(시뮬매니저·EMS·RTDS·공통) 추진 일정 및 체크리스트 |
| [docs/EMS_시험_매뉴얼.md](docs/EMS_시험_매뉴얼.md) | EMS 시험 페이지 사용 매뉴얼 — 화면 항목 설명, 표준 시험 순서, 예외 시험, FAQ |
| [docs/WPF_PORT.md](docs/WPF_PORT.md) | WPF 포팅판(SimulManager/) — DevExpress WPF+SciChart 병행 구현, Python↔C# 구조 대응표 |

**유지관리 규칙:**
1. 구현이 원본 설계 문서(xlsx/docx)와 어긋나면 **문서가 우선**한다.
2. **추가 개발·수정 시 위 md 문서에서 해당하는 부분을 찾아 함께 수정 또는 추가한다.**
   (예: 레지스터/스케일 변경 → PROTOCOL_RULES.md, 계획 변경 → DEVELOPMENT_PLAN.md,
   협의 항목 확정 → OPEN_ISSUES.md 상태 갱신 + 관련 문서 반영)
3. CLAUDE.md는 **200줄 이하**를 유지한다. 내용을 추가해야 하면 주제별로 세분화해
   `docs/` 아래 새 md 파일로 만들고 위 표에 링크를 추가한 뒤 그 파일에 작성한다.
4. Phase 완료 시 아래 '개발 단계 현황' 체크박스를 갱신한다.

## 시스템 구조 (링 토폴로지 — 상세: PROTOCOL_RULES.md)

```
링크① EMS ──(Client)──▶ 시뮬매니저(Server) : 시각·기상·이벤트 읽기 / 예측·HB 기록
링크② EMS ──(Client)──▶ RTDS(Server)      : P/Q 지령 쓰기 / 계측·상태 읽기
링크③ RTDS ─(Client)──▶ 시뮬매니저(Server) : 환경·시나리오 읽기 / HB·결과 기록
```

- **시뮬매니저**(본 프로젝트, 예 192.168.0.10): Modbus TCP **Server ×2 링크**(①·③).
  가상시계·시나리오 배포·Historian·생존감시·연결중개만 수행 — 운영 제어에 개입하지 않음.
- **EMS**(예 192.168.0.20, 외부): 예측·최적화·ESS 지령. **RTDS**(예 192.168.0.30, 외부): 소내망·PCS 모델.
- 시뮬레이션: 가상 30일, 15분 제어주기(96구간/일, 총 2,880구간), 1분 계측주기,
  시간가속(예: 실 10 s = 가상 15분 → 1 Run ≈ 실 8시간).

## 기술 스택

| 구분 | 선택 |
|---|---|
| Backend | Python 3.11+ / FastAPI / pymodbus(async) — REST + WebSocket, Modbus Server ×2 |
| Frontend | React + Vite + TypeScript — 다른 PC 브라우저에서 접속 가능(0.0.0.0) |
| Historian | SQLite (리포지토리 패턴으로 추상화, 추후 교체 대비) |
| 에뮬레이터 | Python 독립 프로세스 — 간이 EMS·RTDS, 단독 PC 전체 시퀀스 검증용 |

## 디렉터리 구조 (계획 — Phase 1에서 스캐폴드)

```
backend/app/
  protocol/    # ★레지스터 맵 단일 소스(SSOT): 주소·타입·스케일·부호. 하드코딩 금지
  modbus/      # 링크①·③ 서버, 뱅크 라우팅, read/write 후킹
  clock/       # 가상시계·가속엔진 (자유진행)
  scenario/    # 시나리오 DB: CSV 임포트·보간·1분 배포
  sequence/    # P0~P6 상태머신, 연결중개, SOC 핸드셰이크, Late-join, 예외
  watch/       # Heartbeat 생존감시
  historian/   # SQLite 적재, 예측-실적 페어링(B-9), Run 태깅
  evaluation/  # §7 지표: 피크·요금·예측률
  api/         # REST 라우터, WebSocket 스트림
backend/tests/ · backend/config/(YAML)
emulators/ems_emulator/ · emulators/rtds_emulator/
frontend/ · data/ · docs/
```

## 핵심 규칙 요약 (상세·전체 표는 [PROTOCOL_RULES.md](docs/PROTOCOL_RULES.md) — 반드시 참조)

- 레지스터 참조는 항상 링크 접두 **①②③** 표기. 정의는 `protocol/` SSOT 경유만.
- 부호: **방전 +, 충전 −**. 스케일은 링크마다 다름(②전력 ×100 vs ③ ×10 등). big-endian.
- Seq No.(CMD/RES)는 블록 기록 완료 후 마지막에 +1. 구간 인덱스는 시각·기상 갱신 **후** 갱신.
- **가상시계는 자유진행** — 노드를 기다리지 않음. '일시정지'는 운영자 조작 전용.
- Heartbeat 실 1 s / 판정 3 s. 단절 누적 14구간 초과 시 Run 무효.
- Run A/B는 시나리오 ID·제어모드만 다르고 나머지 조건 동일. Run B도 초기 SOC 50% 재주입.

## 개발 단계 현황 (상세: [DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md))

- [x] **Phase 1 — 기반 골격**: 스캐폴드, 레지스터 맵 코드화, 설정, 가상시계, 최소 UI (2026-07-03 완료)
- [x] **Phase 2 — Modbus 서버 계층**: 링크①·③ 서버, 뱅크 라우팅, 후킹, HB 감시 (2026-07-03 완료)
- [x] **Phase 3 — 시나리오·시퀀스 엔진**: 데이터 로드·1분 배포·이벤트 주입, P0~P6, 예외·Late-join (2026-07-06 완료)
- [x] **Phase 4 — EMS/RTDS 에뮬레이터**: 단독 PC 풀 시퀀스 통합 테스트 (2026-07-06 완료)
- [x] **Phase 5 — Historian·실시간 대시보드**: SQLite, WebSocket, 차트·Run 제어 UI (2026-07-06 완료)
- [ ] **Phase 6 — Run 관리·평가·리포트**: Run A/B 오케스트레이션, 페어링, §7 비교 리포트
- [ ] **Phase 7 — 예외·안정화·배포**: §6 자동 테스트, 실장비 연동 설정, Windows 패키징

## 실행·개발 명령어

**실행은 더블클릭이 기본**: 루트의 `install.bat`(최초 1회) → `run.bat`(서버 기동 + 브라우저 자동 열기).

```bash
# Backend 개발 (backend/)
..\.venv\Scripts\activate      # venv는 루트 .venv (install.bat이 생성)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8100
pytest
# Frontend 개발 (frontend/)
npm install && npm run dev     # 빌드: npm run build → dist/ 를 FastAPI가 정적 서빙
```

## 코딩 규약

- Backend: 타입 힌트 필수. 도메인 용어는 설계 문서 용어 그대로(`interval_index`, `cmd_seq`,
  `heartbeat`). 가상시각/실시각을 변수명으로 구분(`virtual_*` / `real_*`).
- Frontend: 함수형 컴포넌트 + hooks, WebSocket 스트림 + REST 조회 조합.
- 테스트: 스케일 변환·핸드셰이크·시퀀스는 단위 테스트 필수. 통합 테스트는 에뮬레이터 기반(Phase 4~).
- **`*.bat` 파일은 CP949 인코딩** (한글 Windows 콘솔용) — Edit/Write 도구는 UTF-8로 저장하므로
  bat 수정 시 반드시 Python으로 `encoding="cp949", newline="\r\n"` 재작성할 것.

## 프로젝트 에이전트 (.claude/agents/)

| 에이전트 | 용도 |
|---|---|
| `protocol-guardian` | 코드의 레지스터 주소·스케일·부호·원자성 규칙이 프로토콜맵/PROTOCOL_RULES.md와 정합하는지 검토 (읽기 전용) |
| `doc-maintainer` | 코드 변경 후 docs/*.md에서 영향받는 부분을 찾아 갱신안 제시·반영 |
| `sequence-tester` | 에뮬레이터 기반 시퀀스·예외 시나리오 테스트 실행·결과 분석 |

프로토콜 관련 코드 변경 시 `protocol-guardian`으로 검토하고, 기능 추가·수정 완료 시
`doc-maintainer`로 문서 동기화를 수행한다.
