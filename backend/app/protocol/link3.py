"""링크③ 시뮬매니저(서버)–RTDS(클라) — 프로토콜맵 시트3.

매니저(보유) → RTDS 읽기: 시각·시나리오·기상·부하·초기SOC (IR·DI)
RTDS → 매니저 쓰기: Heartbeat·결과 이력 (HR)·동작 상태 (Coil) — 규격 정합
"""
from .types import DType, Link, LinkMap, ObjType, RegisterDef

_L = Link.LINK3
_regs: list[RegisterDef] = [
    # ── Input Registers (FC04) — 매니저 보유 / RTDS 읽기 ──
    RegisterDef(_L, ObjType.IR, 0, "accel_factor", "시간 가속 배율", DType.UINT16, 1, "s", "1~900"),
    RegisterDef(_L, ObjType.IR, 1, "interval_index", "현재 15분 구간 인덱스", DType.UINT16, 1, "", "0~95"),
    RegisterDef(_L, ObjType.IR, 2, "scenario_id", "시나리오 ID", DType.UINT16, 1, "", "0~65535"),
    RegisterDef(_L, ObjType.IR, 3, "ems_status", "EMS 상태", DType.UINT16, 1, "",
                "0해제/1연결 — 0이면 RTDS는 EMS 지령 무시·안전 유지"),
    RegisterDef(_L, ObjType.IR, 4, "irradiance", "일사량", DType.UINT16, 1, "MJ/m²",
                "0~1500, PV 모델 입력 — v1.0에서 W/m²→MJ/m² 변경"),
    RegisterDef(_L, ObjType.IR, 5, "ambient_temp", "외기온도", DType.INT16, 100, "℃",
                "±327.67 — 링크①은 ×10(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.IR, 6, "snow_depth", "적설량", DType.UINT16, 100, "cm",
                "0~655.35 — 링크①은 ×10(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.IR, 7, "weather_code", "기상 상태코드", DType.UINT16, 1, "",
                "0정상/1폭염/2폭설"),
    RegisterDef(_L, ObjType.IR, 8, "load_profile_ref", "부하 프로파일 기준값", DType.UINT16, 100, "kW",
                "0~655.35 — RTDS로만 전달(EMS 블라인드)"),
    RegisterDef(_L, ObjType.IR, 9, "pv_power", "PV 발전량", DType.UINT16, 100, "kW",
                "0~655.35 — (옵션) 모델 미사용 시 직접 주입, 검증용"),
    RegisterDef(_L, ObjType.IR, 10, "load_power", "부하량", DType.UINT16, 100, "kW",
                "0~655.35 — (옵션) 검증용 참조"),
    RegisterDef(_L, ObjType.IR, 11, "initial_soc", "초기 SOC 값", DType.UINT16, 100, "%",
                "0~100, 기동 시 RTDS 주입"),
    # ── Discrete Inputs (FC02) — 매니저 보유 / RTDS 읽기 (지령 비트) ──
    RegisterDef(_L, ObjType.DI, 0, "initial_soc_trigger", "초기 SOC 적용 트리거", DType.BIT,
                note="1일 때 RTDS가 IR 300011 주입 — 최초 기동에만 세트"),
    RegisterDef(_L, ObjType.DI, 1, "ess_run_enable_sim", "ESS 운전 Enable(시뮬)", DType.BIT,
                note="시뮬 차원 허용, EMS Enable과 AND"),
    RegisterDef(_L, ObjType.DI, 2, "reset", "리셋", DType.BIT, note="Run 전환 시 모델 상태 초기화"),
    # ── Holding Registers (FC06/16) — RTDS → 매니저 쓰기 (HB·결과 이력) ──
    RegisterDef(_L, ObjType.HR, 0, "rtds_heartbeat", "RTDS Heartbeat", DType.UINT16, 1, "",
                "실 1s 증가, 생존감시"),
    RegisterDef(_L, ObjType.HR, 1, "rtds_status_code", "RTDS 상태코드", DType.UINT16, 1, "",
                "0정상/1주의/2고장"),
    RegisterDef(_L, ObjType.HR, 2, "ess_power", "ESS 충방전 전력", DType.INT16, 10, "kW",
                "±3276.7, Historian — 링크②는 ×100(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.HR, 3, "ess_soc", "ESS SOC", DType.UINT16, 100, "%",
                "0~100, 보존 SOC 갱신원"),
    RegisterDef(_L, ObjType.HR, 4, "pcc_import", "PCC 수전전력", DType.UINT16, 10, "kW",
                "0~6553.5, 요금 산정 — 링크②는 ×100(스케일 상이 주의)"),
    RegisterDef(_L, ObjType.HR, 5, "pcc_export", "PCC 역송전력", DType.UINT16, 10, "kW", "0~6553.5"),
    # ── Coils (FC05/15) — RTDS → 매니저 쓰기 (동작·상태 비트) ──
    RegisterDef(_L, ObjType.COIL, 0, "rtds_running", "RTDS 운전중", DType.BIT,
                note="★연결중개 출발점: 매니저 감지 → ①Coil 000004 세트"),
    RegisterDef(_L, ObjType.COIL, 1, "initial_soc_ack", "초기 SOC 반영완료", DType.BIT,
                note="SOC 주입 ack — 매니저 확인 후 트리거 해제"),
]

LINK3 = LinkMap(link=_L, server="시뮬매니저", client="RTDS", registers=_regs)
