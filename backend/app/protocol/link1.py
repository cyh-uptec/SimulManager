"""링크① EMS(클라)–시뮬매니저(서버) — 프로토콜맵 시트1.

매니저(보유) → EMS 읽기: 운전 지령 Coil·시각/기상 HR
EMS → 매니저 쓰기: 상태 DI·예측/이력 IR (구현: HR 미러 경유, docs/OPEN_ISSUES.md #2)
"""
from .types import DType, Link, LinkMap, ObjType, RegisterDef

_L = Link.LINK1
_regs: list[RegisterDef] = [
    # ── Coils (FC05/15) — 매니저 → EMS 읽기 (지령·연결중개) ──
    RegisterDef(_L, ObjType.COIL, 0, "run_start", "운전 시작", DType.BIT, note="시뮬레이션 시작"),
    RegisterDef(_L, ObjType.COIL, 1, "run_stop", "운전 정지", DType.BIT),
    RegisterDef(_L, ObjType.COIL, 2, "pause", "일시정지", DType.BIT, note="운영자 조작 전용"),
    RegisterDef(_L, ObjType.COIL, 3, "reset", "리셋", DType.BIT, note="시퀀스/오류 리셋"),
    RegisterDef(_L, ObjType.COIL, 4, "rtds_ready", "RTDS 준비됨(연결가능)", DType.BIT,
                note="★연결중개: 매니저가 EMS에 RTDS 접속 가능 통지"),
    RegisterDef(_L, ObjType.COIL, 5, "ems_enable", "EMS Enable", DType.BIT, note="EMS 연산 활성"),
    # ── Holding Registers (FC06/16) — 매니저 → EMS 읽기 (시각·기상) ──
    RegisterDef(_L, ObjType.HR, 0, "sim_year", "SIM 가상 연도", DType.UINT16, 1, "year", "2000~2100"),
    RegisterDef(_L, ObjType.HR, 1, "sim_month", "SIM 가상 월", DType.UINT16, 1, "month", "1~12"),
    RegisterDef(_L, ObjType.HR, 2, "sim_day", "SIM 가상 일", DType.UINT16, 1, "day", "1~31"),
    RegisterDef(_L, ObjType.HR, 3, "sim_hour", "SIM 가상 시", DType.UINT16, 1, "hour", "0~23"),
    RegisterDef(_L, ObjType.HR, 4, "sim_minute", "SIM 가상 분", DType.UINT16, 1, "min", "0~59"),
    RegisterDef(_L, ObjType.HR, 5, "accel_factor", "시간 가속 배율", DType.UINT16, 1, "s",
                "1~900, 실 15분→설정 초"),
    RegisterDef(_L, ObjType.HR, 6, "interval_index", "현재 15분 구간 인덱스", DType.UINT16, 1, "",
                "0~95, 하루 96구간"),
    RegisterDef(_L, ObjType.HR, 7, "scenario_id", "시나리오 ID", DType.UINT16, 1, "",
                "1=규칙기반 / 2=최적"),
    RegisterDef(_L, ObjType.HR, 8, "irradiance", "일사량", DType.UINT16, 1, "MJ/m²",
                "0~1500 — v1.0에서 W/m²→MJ/m² 변경 (1시간 적산, 실측 원본 단위 그대로)"),
    RegisterDef(_L, ObjType.HR, 9, "ambient_temp", "외기온도", DType.INT16, 10, "℃",
                "±3276.7 — 링크③은 ×100(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.HR, 10, "snow_depth", "적설량", DType.UINT16, 10, "cm",
                "0~6553.5 — 링크③은 ×100(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.HR, 11, "weather_code", "기상 상태코드", DType.UINT16, 1, "",
                "0정상/1폭염/2폭설"),
    # ── Discrete Inputs (FC02) — EMS → 매니저 쓰기 (상태) ──
    RegisterDef(_L, ObjType.DI, 0, "ems_ready", "EMS 준비완료", DType.BIT),
    RegisterDef(_L, ObjType.DI, 1, "ems_computing", "EMS 연산중", DType.BIT),
    RegisterDef(_L, ObjType.DI, 2, "forecast_done", "예측 완료", DType.BIT, note="당 구간 예측 산정완료"),
    RegisterDef(_L, ObjType.DI, 3, "ems_alarm", "EMS 알람", DType.BIT),
    # ── Input Registers (FC04) — EMS → 매니저 쓰기 (예측·이력) ──
    RegisterDef(_L, ObjType.IR, 0, "ems_heartbeat", "EMS Heartbeat", DType.UINT16, 1, "",
                "실 1s 증가, 생존감시"),
    RegisterDef(_L, ObjType.IR, 1, "ems_status_code", "EMS 상태코드", DType.UINT16, 1, "",
                "0정상/1주의/2고장"),
    RegisterDef(_L, ObjType.IR, 2, "forecast_base_interval", "예측 기준 구간", DType.UINT16, 1, "",
                "0~95, [1구간] 예측의 기준 구간(v1.7 — 전달은 1구간만)"),
]

# [1~4구간] PV 예측·부하 예측·ESS 충방전 스케줄·목표 SOC (IR 300003~300018)
# 예측 블록은 [1구간](300003~300006)만 전달 — [2~4구간](300007~300018)은 예약, 0 기록 (설계서 v1.7)
_RESERVED = "예약(미사용, 0 기록) — 예측 블록은 [1구간]만 전달(v1.7)"
for n in range(1, 5):
    base = 3 + (n - 1) * 4
    _regs += [
        RegisterDef(_L, ObjType.IR, base + 0, f"pv_forecast_{n}", f"[{n}구간] PV 예측",
                    DType.UINT16, 100, "kW", "0~655.35" if n == 1 else _RESERVED),
        RegisterDef(_L, ObjType.IR, base + 1, f"load_forecast_{n}", f"[{n}구간] 부하 예측",
                    DType.UINT16, 100, "kW", "0~655.35" if n == 1 else _RESERVED),
        RegisterDef(_L, ObjType.IR, base + 2, f"ess_schedule_{n}", f"[{n}구간] ESS 충방전 스케줄",
                    DType.INT16, 100, "kW", "±250.00, +방전/−충전" if n == 1 else _RESERVED),
        RegisterDef(_L, ObjType.IR, base + 3, f"target_soc_{n}", f"[{n}구간] 목표 SOC",
                    DType.UINT16, 100, "%", "0~100" if n == 1 else _RESERVED),
    ]

LINK1 = LinkMap(link=_L, server="시뮬매니저", client="EMS", registers=_regs)
