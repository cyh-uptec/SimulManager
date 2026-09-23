# WPF 포팅판 (SimulManager/ — DevExpress WPF + SciChart)

Python backend + React frontend와 **동일 기능**을 단일 WPF 데스크톱 앱으로 포팅한 병행 구현.
위치: `SimulManager/SimulManager.sln` (.NET Framework 4.8, DevExpress 23.1.15, SciChart 7.0.2).

## 구조 대응표

| Python (backend/app) | C# (SimulManager/Services) | 비고 |
|---|---|---|
| `protocol/` (SSOT) | `Protocol/Types.cs·Codec.cs·Links.cs` | 링크①②③ 맵·스케일·미러 상수 동일 |
| `modbus/bank.py` | `Modbus/RegisterBank.cs` | 쓰기 후킹·①HR 미러 매핑 동일 |
| `modbus/server.py` (pymodbus) | `Modbus/ModbusTcpSlave.cs` (자체 구현) | FC 1/2/3/4/5/6/15/16, RoutingProxy 포함 |
| `clock/virtual_clock.py` | `Clock/VirtualClock.cs` | 자유진행·드리프트 보정 동일 |
| `scenario/` (pandas) | `Scenario/` | CSV utf-8/cp949 자동 판별, xlsx는 DevExpress Office API |
| `sequence/engine.py` | `Sequence/SequenceEngine.cs` | P0~P6·연결중개·SOC 핸드셰이크 동일 |
| `watch/heartbeat.py` | `Watch/HeartbeatMonitor.cs` | 1s/3s 판정 동일 |
| `historian/` (SQLite) | `Historian/` (JSON/JSONL 파일) | 스키마 동등: runs.json·measurements-{run}.jsonl·forecasts-{run}.jsonl·seqlog.jsonl |
| `events.py` (asyncio 버스) | `EventBus.cs` (엔진 스레드 1개) | asyncio 단일 스레드 의미론 재현 |
| `api/` REST·WebSocket | `SimulatorHost.cs` + ViewModel 직접 구독 | 프로세스 내 직접 호출로 대체 |

| React (frontend/src) | WPF (SimulManager/Views·ViewModels) |
|---|---|
| `App.tsx` 사이드바 | `MainWindow.xaml` — DevExpress `HamburgerMenu` (Home·모니터링·데이터·시퀀스) |
| `HomePage.tsx` | `HomeView` — 스탯카드 5개·도넛(SVG→`DonutControl`)·노드 카드·Run 제어·최근 로그 |
| `MonitorPage.tsx` (recharts) | `MonitorView` — SciChart 전력/SOC 차트, Run 선택·실시간 append |
| `DataPage.tsx` | `DataView` — 업로드 카드·세그먼트·컬럼 체크·차트·기상 이벤트·정렬/페이지 표 |
| `SequencePage.tsx` | `SequenceView` — 50건 페이지·offset 0 실시간 갱신(400ms 병합) |
| `SettingsModal.tsx` | `SettingsDialog` — IDLE에서만 저장, user_settings.json 영속화 |
| `index.css` 팔레트 | `Styles/Palette.xaml` — #F2F5FB 배경·인디고→블루 그라데이션·카드 스타일 동일 |

## 데이터·설정 위치

실행 파일 옆 `bin\Debug\data\` (배포 시 exe 옆 `data\`):
`uploads\`(부하·기상 원본+meta), `historian\`(runs·계측·예측·시퀀스 로그), `events.json`,
`persisted_soc.json`, `config\user_settings.json`.

## 배포 (xcopy 방식)

Release 빌드 후 `bin\Release\` 폴더째 복사하면 다른 PC에서 그대로 실행된다 (2026-08-04 검증):
- DevExpress 24종·SciChart 4종 DLL이 출력 폴더에 전부 복사됨(의존성 클로저 확인) — 대상 PC에
  DevExpress/SciChart 설치 불필요. SciChart 네이티브 엔진은 첫 실행 시 %LocalAppData%에 자동 추출.
- 대상 PC 요구사항: **.NET Framework 4.8** (Win10 1903+/Win11 기본 탑재).
- **방화벽 인바운드 허용 필수**: 5020·5021 (single_port 모드는 502) — 앱이 Modbus TCP 서버이므로.
- 시나리오 데이터·설정을 가져가려면 exe 옆 `data\` 폴더를 함께 복사 (없으면 첫 실행 시 빈 폴더 생성).

## 유지관리 주의

- 레지스터 맵을 변경하면 **양쪽**(backend/app/protocol ↔ Services/Protocol/Links.cs)을 함께 수정.
- Historian은 SQLite 대신 파일 기반(JSONL) — NuGet CLI 부재 환경에서 네이티브 SQLite 의존성 회피 목적.
  스키마·조회(다운샘플·페이지네이션) 의미는 동일.
- 포트 기본값 5020/5021(per_link_port)·single_port(502) 라우팅 프록시 모두 지원 — 기존 에뮬레이터
  **주의(2026-08-11)**: 웹판 시험 기본 포트가 ①7000/③5021로 변경됨 — WPF판(C# `AppConfig.Link1Port=5020`)은
  추후 재개 시 7000으로 동기화할 것.
  (`emulators/`)가 옵션 변경 없이 그대로 연동됨 (스모크 테스트 확인, 2026-08-04).
