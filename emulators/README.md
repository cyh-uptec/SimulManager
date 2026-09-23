# EMS / RTDS 에뮬레이터 (Phase 4)

시뮬매니저를 **단독 PC에서 전체 시퀀스(P0~P6)로 검증**하기 위한 간이 상대 노드.
레지스터 주소·스케일은 `backend/app/protocol/`(SSOT)을 직접 import — 실제 EMS·RTDS
개발측에 **참조 구현**으로 공유 가능하다.

## 실행

```
run.bat            # ① 매니저 먼저 실행
run_emulators.bat  # ② RTDS·EMS 에뮬레이터 두 창 실행 (더블클릭)
```
이후 대시보드에서 EMS/RTDS 노드가 '정상'이 되면 [Run 시작].

개별 실행·옵션:
```bash
.venv\Scripts\python.exe emulators\rtds_emulator.py --manager 127.0.0.1 --manager-port 5021 --listen-port 5022
.venv\Scripts\python.exe emulators\ems_emulator.py  --manager 127.0.0.1 --manager-port 7000 --rtds 127.0.0.1 --rtds-port 5022
```

## rtds_emulator.py — 링크② Server(:5022) + 링크③ Client

- 환경 폴링: ③IR(가속·구간·기상·부하), ③DI(SOC 트리거·Enable·리셋)
- 간이 모델: PV 150 kW(일사 비례, 온도 디레이팅, 적설 저감), 배터리 500 kWh SOC 적분
  (효율 95%, 1%=5 kWh), P지령 램프 추종 + SOC 상하한·출력 제한 인터록
- PCC 수전/역송 = 부하 − PV − P_ESS
- **CMD Seq 변화 시에만 지령 래치**(원자성), 지령 echo(②IR 300031/32) 기록
- **워치독 3가상분 정체 → 안전상태(P=0, 상태워드=대기)**
- 계측 블록 기록 후 **RES Seq 마지막 +1**, 누적 충·방전 전력량 UINT32(×10 kWh)
- 초기 SOC 레벨 핸드셰이크(트리거→주입→ack→해제), P6 리셋 시 누적량 초기화
- Heartbeat: ③HR 400000 실 1 s

## ems_emulator.py — 링크①·② Client

- 링크① 폴링: 시각·기상·구간·운전 Coil. **①'RTDS 준비됨'=1 확인 후 링크② 접속**(연결중개)
- EMS 쓰기는 HR 미러 경유: HB(430000), 상태 DI(410000대), 예측 블록(430002~430006 — [1구간]만 유효, 430007~430018은 예약 0 기록, 설계서 v1.7)
- 구간 경계 트리거: 예측(간이) → 스케줄 → 예측 블록 기록 → **P지령 기록 후 CMD Seq 마지막 +1**
- Run A(시나리오 1): TOU 규칙 — 23~09시 충전 −40 kW / 10~12·13~17시 방전 / 그 외 0 (§5.1)
- Run B(시나리오 2): 간이 피크저감 — 이동평균(96구간) 초과 부하를 방전 상쇄 + 야간 충전
- 가상 1분: 계측 스냅샷(RES Seq 게이트) + 워치독 +1. 운전 정지 Coil 감지 시 P=0·Enable 해제

## 통합 테스트

`backend/tests/test_emulators_integration.py` — 매니저 스택 + 에뮬레이터 2종을 자동
기동해 연결중개·핸드셰이크·지령·계측 왕복을 검증 (`pytest -m slow`, 약 15초).
