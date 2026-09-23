// 링크①②③ 레지스터 맵 정의 — 프로토콜맵 v1.1 시트1~3
// (backend/app/protocol/link1.py·link2.py·link3.py·mirror.py 포팅).
using System.Collections.Generic;

namespace SimulManager.Services.Protocol
{
    /// <summary>링크① DI/IR 쓰기용 HR 미러 주소 — docs/OPEN_ISSUES.md #2.
    /// Modbus 규격상 Client(EMS)는 DI/IR에 쓸 수 없으므로, EMS는 합의된 미러 HR 주소에
    /// FC16으로 기록하고 매니저가 내부적으로 DI/IR 뱅크에 매핑한다 (시퀀스 설계서 §8 옵션 b).</summary>
    public static class Mirror
    {
        public const int HrBaseDi = 10000; // DI n ↔ HR (10000 + n), 참조 410000대
        public const int HrBaseIr = 30000; // IR n ↔ HR (30000 + n), 참조 430000대
    }

    public static class Links
    {
        public static readonly LinkMap Link1Map = BuildLink1();
        public static readonly LinkMap Link2Map = BuildLink2();
        public static readonly LinkMap Link3Map = BuildLink3();

        // ── 링크① EMS(클라)–시뮬매니저(서버) — 시트1 ─────────────────────
        private static LinkMap BuildLink1()
        {
            var l = Link.Link1;
            var regs = new List<RegisterDef>
            {
                // Coils (FC05/15) — 매니저 → EMS 읽기 (지령·연결중개)
                new RegisterDef(l, ObjType.Coil, 0, "run_start", "운전 시작", DType.Bit, note: "시뮬레이션 시작"),
                new RegisterDef(l, ObjType.Coil, 1, "run_stop", "운전 정지", DType.Bit),
                new RegisterDef(l, ObjType.Coil, 2, "pause", "일시정지", DType.Bit, note: "운영자 조작 전용"),
                new RegisterDef(l, ObjType.Coil, 3, "reset", "리셋", DType.Bit, note: "시퀀스/오류 리셋"),
                new RegisterDef(l, ObjType.Coil, 4, "rtds_ready", "RTDS 준비됨(연결가능)", DType.Bit,
                    note: "★연결중개: 매니저가 EMS에 RTDS 접속 가능 통지"),
                new RegisterDef(l, ObjType.Coil, 5, "ems_enable", "EMS Enable", DType.Bit, note: "EMS 연산 활성"),
                // Holding Registers (FC06/16) — 매니저 → EMS 읽기 (시각·기상)
                new RegisterDef(l, ObjType.Hr, 0, "sim_year", "SIM 가상 연도", DType.UInt16, 1, "year", "2000~2100"),
                new RegisterDef(l, ObjType.Hr, 1, "sim_month", "SIM 가상 월", DType.UInt16, 1, "month", "1~12"),
                new RegisterDef(l, ObjType.Hr, 2, "sim_day", "SIM 가상 일", DType.UInt16, 1, "day", "1~31"),
                new RegisterDef(l, ObjType.Hr, 3, "sim_hour", "SIM 가상 시", DType.UInt16, 1, "hour", "0~23"),
                new RegisterDef(l, ObjType.Hr, 4, "sim_minute", "SIM 가상 분", DType.UInt16, 1, "min", "0~59"),
                new RegisterDef(l, ObjType.Hr, 5, "accel_factor", "시간 가속 배율", DType.UInt16, 1, "s",
                    "1~900, 실 15분→설정 초"),
                new RegisterDef(l, ObjType.Hr, 6, "interval_index", "현재 15분 구간 인덱스", DType.UInt16, 1, "",
                    "0~95, 하루 96구간"),
                new RegisterDef(l, ObjType.Hr, 7, "scenario_id", "시나리오 ID", DType.UInt16, 1, "",
                    "1=규칙기반 / 2=최적"),
                new RegisterDef(l, ObjType.Hr, 8, "irradiance", "일사량", DType.UInt16, 1, "MJ/m²",
                    "0~1500 — v1.0에서 W/m²→MJ/m² 변경 (1시간 적산, 실측 원본 단위 그대로)"),
                new RegisterDef(l, ObjType.Hr, 9, "ambient_temp", "외기온도", DType.Int16, 10, "℃",
                    "±3276.7 — 링크③은 ×100(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Hr, 10, "snow_depth", "적설량", DType.UInt16, 10, "cm",
                    "0~6553.5 — 링크③은 ×100(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Hr, 11, "weather_code", "기상 상태코드", DType.UInt16, 1, "",
                    "0정상/1폭염/2폭설"),
                // Discrete Inputs (FC02) — EMS → 매니저 쓰기 (상태)
                new RegisterDef(l, ObjType.Di, 0, "ems_ready", "EMS 준비완료", DType.Bit),
                new RegisterDef(l, ObjType.Di, 1, "ems_computing", "EMS 연산중", DType.Bit),
                new RegisterDef(l, ObjType.Di, 2, "forecast_done", "예측 완료", DType.Bit, note: "당 구간 예측 산정완료"),
                new RegisterDef(l, ObjType.Di, 3, "ems_alarm", "EMS 알람", DType.Bit),
                // Input Registers (FC04) — EMS → 매니저 쓰기 (예측·이력)
                new RegisterDef(l, ObjType.Ir, 0, "ems_heartbeat", "EMS Heartbeat", DType.UInt16, 1, "",
                    "실 1s 증가, 생존감시"),
                new RegisterDef(l, ObjType.Ir, 1, "ems_status_code", "EMS 상태코드", DType.UInt16, 1, "",
                    "0정상/1주의/2고장"),
                new RegisterDef(l, ObjType.Ir, 2, "forecast_base_interval", "예측 기준 구간", DType.UInt16, 1, "",
                    "0~95, [1구간] 예측의 기준 구간(v1.7 — 전달은 1구간만)"),
            };

            // [1~4구간] PV 예측·부하 예측·ESS 충방전 스케줄·목표 SOC (IR 300003~300018)
            // 예측 블록은 [1구간](300003~300006)만 전달 — [2~4구간](300007~300018)은 예약, 0 기록 (설계서 v1.7)
            const string reserved = "예약(미사용, 0 기록) — 예측 블록은 [1구간]만 전달(v1.7)";
            for (int n = 1; n <= 4; n++)
            {
                int b = 3 + (n - 1) * 4;
                regs.Add(new RegisterDef(l, ObjType.Ir, b + 0, "pv_forecast_" + n, "[" + n + "구간] PV 예측",
                    DType.UInt16, 100, "kW", n == 1 ? "0~655.35" : reserved));
                regs.Add(new RegisterDef(l, ObjType.Ir, b + 1, "load_forecast_" + n, "[" + n + "구간] 부하 예측",
                    DType.UInt16, 100, "kW", n == 1 ? "0~655.35" : reserved));
                regs.Add(new RegisterDef(l, ObjType.Ir, b + 2, "ess_schedule_" + n, "[" + n + "구간] ESS 충방전 스케줄",
                    DType.Int16, 100, "kW", n == 1 ? "±250.00, +방전/−충전" : reserved));
                regs.Add(new RegisterDef(l, ObjType.Ir, b + 3, "target_soc_" + n, "[" + n + "구간] 목표 SOC",
                    DType.UInt16, 100, "%", n == 1 ? "0~100" : reserved));
            }

            return new LinkMap(l, "시뮬매니저", "EMS", regs);
        }

        // ── 링크② RTDS(서버)–EMS(클라) — 시트2 (매니저는 당사자 아님, 참조용) ──
        private static LinkMap BuildLink2()
        {
            var l = Link.Link2;
            var regs = new List<RegisterDef>
            {
                new RegisterDef(l, ObjType.Coil, 0, "ess_run_enable", "ESS 운전 Enable", DType.Bit, note: "ESS ON/OFF"),
                new RegisterDef(l, ObjType.Coil, 1, "control_enable", "제어 Enable", DType.Bit, note: "제어 지령 활성"),
                new RegisterDef(l, ObjType.Coil, 2, "e_stop", "비상정지(E-Stop)", DType.Bit, note: "안전 정지"),
                new RegisterDef(l, ObjType.Coil, 3, "discharge_enable", "PCS 방전 Enable", DType.Bit),
                new RegisterDef(l, ObjType.Coil, 4, "charge_enable", "PCS 충전 Enable", DType.Bit),
                new RegisterDef(l, ObjType.Coil, 5, "fault_reset", "고장 리셋(Fault Reset)", DType.Bit, note: "PCS 고장 해제"),
                new RegisterDef(l, ObjType.Hr, 0, "ess_control_mode", "ESS 제어 모드", DType.UInt16, 1, "",
                    "0자동/1수동/2경제/3피크저감 — Run A=2, Run B=3"),
                new RegisterDef(l, ObjType.Hr, 1, "p_command", "유효전력 P 지령", DType.Int16, 100, "kW", "±250.00, +방전/−충전"),
                new RegisterDef(l, ObjType.Hr, 2, "q_command", "무효전력 Q 지령", DType.Int16, 100, "kvar", "±250.00, +진상/−지상"),
                new RegisterDef(l, ObjType.Hr, 3, "pf_command", "역률 지령", DType.Int16, 1000, "", "−1.000~1.000"),
                new RegisterDef(l, ObjType.Hr, 4, "reactive_mode", "무효전력 제어 모드", DType.UInt16, 1, "", "0:Q고정/1:PF고정/2:Q(V)"),
                new RegisterDef(l, ObjType.Hr, 5, "target_soc", "ESS 목표 SOC", DType.UInt16, 100, "%", "0~100"),
                new RegisterDef(l, ObjType.Hr, 6, "soc_max", "SOC 운전 상한", DType.UInt16, 100, "%", "0~100"),
                new RegisterDef(l, ObjType.Hr, 7, "soc_min", "SOC 운전 하한", DType.UInt16, 100, "%", "0~100"),
                new RegisterDef(l, ObjType.Hr, 8, "discharge_limit", "방전량 제한", DType.UInt16, 100, "kW", "0~655.35, HMI_Plim_pos"),
                new RegisterDef(l, ObjType.Hr, 9, "charge_limit", "충전량 제한", DType.UInt16, 100, "kW", "0~655.35, HMI_Plim_neg"),
                new RegisterDef(l, ObjType.Hr, 10, "controller_watchdog", "컨트롤러 워치독", DType.UInt16, 1, "",
                    "EMS 주기 +1, 3주기 정체 시 PCS 안전상태"),
                new RegisterDef(l, ObjType.Hr, 11, "cmd_seq", "CMD Sequence No.", DType.UInt16, 1, "",
                    "신규 지령 +1 — 블록 기록 완료 후 마지막에 증가(원자성)"),
                new RegisterDef(l, ObjType.Di, 0, "rtds_running", "RTDS 운전중", DType.Bit),
                new RegisterDef(l, ObjType.Di, 1, "ess_charging", "ESS 충전중", DType.Bit),
                new RegisterDef(l, ObjType.Di, 2, "ess_discharging", "ESS 방전중", DType.Bit),
                new RegisterDef(l, ObjType.Di, 3, "ess_ready", "ESS 가용(Ready)", DType.Bit),
                new RegisterDef(l, ObjType.Di, 4, "grid_tied", "계통 연계(Grid-tied)", DType.Bit, note: "연계=1/해렬=0"),
                new RegisterDef(l, ObjType.Di, 5, "rtds_pcs_fault", "RTDS/PCS 고장", DType.Bit, note: "상세는 고장 워드 IR 300029"),
                new RegisterDef(l, ObjType.Ir, 0, "res_seq", "RES Sequence No.", DType.UInt16, 1, "",
                    "신규 결과 +1 — 변화 시에만 스냅샷 래치"),
                new RegisterDef(l, ObjType.Ir, 1, "result_interval", "결과 해당 구간", DType.UInt16, 1, "", "0~95"),
                new RegisterDef(l, ObjType.Ir, 2, "pv_power", "PV 발전량", DType.UInt16, 100, "kW", "0~655.35"),
                new RegisterDef(l, ObjType.Ir, 3, "load_power", "부하량(실측)", DType.UInt16, 100, "kW",
                    "0~655.35 — 부하 예측률 평가 ground truth"),
                new RegisterDef(l, ObjType.Ir, 4, "ess_power", "ESS 충방전 전력(실제)", DType.Int16, 100, "kW", "±327.67, +방전/−충전"),
                new RegisterDef(l, ObjType.Ir, 5, "ess_soc", "ESS SOC(요약)", DType.UInt16, 100, "%", "0~100"),
                new RegisterDef(l, ObjType.Ir, 6, "pcc_import", "PCC 수전전력(import)", DType.UInt16, 100, "kW", "0~655.35, 요금 산정"),
                new RegisterDef(l, ObjType.Ir, 7, "pcc_export", "PCC 역송전력(export)", DType.UInt16, 100, "kW", "0~655.35"),
                new RegisterDef(l, ObjType.Ir, 8, "pcc_voltage", "PCC 전압", DType.UInt16, 100, "V", "0~655.35"),
                new RegisterDef(l, ObjType.Ir, 9, "grid_freq", "계통 주파수", DType.UInt16, 100, "Hz", "예 60.00"),
                new RegisterDef(l, ObjType.Ir, 10, "v_ab", "계통 A-B 선간전압", DType.UInt16, 100, "V", "HMI_Va"),
                new RegisterDef(l, ObjType.Ir, 11, "v_bc", "계통 B-C 선간전압", DType.UInt16, 100, "V", "HMI_Vb"),
                new RegisterDef(l, ObjType.Ir, 12, "v_ca", "계통 C-A 선간전압", DType.UInt16, 100, "V", "HMI_Vc"),
                new RegisterDef(l, ObjType.Ir, 13, "i_a", "A상 전류", DType.UInt16, 100, "A", "HMI_Ia"),
                new RegisterDef(l, ObjType.Ir, 14, "i_b", "B상 전류", DType.UInt16, 100, "A", "HMI_Ib"),
                new RegisterDef(l, ObjType.Ir, 15, "i_c", "C상 전류", DType.UInt16, 100, "A", "HMI_Ic"),
                new RegisterDef(l, ObjType.Ir, 16, "pcs_p", "PCS 유효전력(계측)", DType.Int16, 100, "kW", "±327.67, HMI_Pac"),
                new RegisterDef(l, ObjType.Ir, 17, "pcs_q", "PCS 무효전력 Q(계측)", DType.Int16, 100, "kvar", "±327.67, +진상/−지상"),
                new RegisterDef(l, ObjType.Ir, 18, "pcs_s", "PCS 피상전력 S(계측)", DType.UInt16, 100, "kVA", "0~655.35"),
                new RegisterDef(l, ObjType.Ir, 19, "pcs_pf", "역률 PF(계측)", DType.Int16, 1000, "", "−1.000~1.000"),
                new RegisterDef(l, ObjType.Ir, 20, "dc_link_voltage", "DC 링크 전압", DType.UInt16, 50, "V", "0~1310.70, HMI_Vdc"),
                new RegisterDef(l, ObjType.Ir, 21, "bank_soc", "ESS Bank SOC", DType.UInt16, 10, "%", "0~1000, 0.1% 분해능"),
                new RegisterDef(l, ObjType.Ir, 22, "bank_soh", "ESS Bank SOH", DType.UInt16, 10, "%", "0~1000, 0.1% 분해능"),
                new RegisterDef(l, ObjType.Ir, 23, "bank_voltage", "ESS Bank 전압", DType.UInt16, 50, "V", "0~1310.70"),
                new RegisterDef(l, ObjType.Ir, 24, "bank_current", "ESS Bank 전류", DType.Int16, 50, "A", "±655.34, +방전(DC)"),
                new RegisterDef(l, ObjType.Ir, 25, "bank_power", "ESS Bank 전력", DType.Int16, 100, "kW", "±327.67, −충전"),
                new RegisterDef(l, ObjType.Ir, 26, "pcs_temp", "PCS 온도", DType.Int16, 10, "℃", "±3276.7, IGBT/내부"),
                new RegisterDef(l, ObjType.Ir, 27, "battery_temp", "배터리 온도", DType.Int16, 10, "℃", "±3276.7, 최고 셀"),
                new RegisterDef(l, ObjType.Ir, 28, "pcs_state_word", "PCS 운전상태 워드", DType.UInt16, 1, "",
                    "0정지/1대기/2운전/3계통연계/4고장"),
                new RegisterDef(l, ObjType.Ir, 29, "fault_word", "고장·알람 워드", DType.UInt16, 1, "",
                    "비트맵: b0과전압/b1과전류/b2과온/b3지락/b4계통/b5통신"),
                new RegisterDef(l, ObjType.Ir, 30, "rtds_status_code", "RTDS 상태코드", DType.UInt16, 1, "", "0정상/1주의/2고장"),
                new RegisterDef(l, ObjType.Ir, 31, "discharge_cmd_echo", "방전 지령 echo", DType.UInt16, 100, "kW", "Order_P_pos(적용값)"),
                new RegisterDef(l, ObjType.Ir, 32, "charge_cmd_echo", "충전 지령 echo", DType.UInt16, 100, "kW", "Order_P_neg(적용값)"),
                new RegisterDef(l, ObjType.Ir, 33, "discharge_energy_hi", "누적 방전 전력량(Hi)", DType.UInt16, 1, "", "UINT32 상위워드"),
                new RegisterDef(l, ObjType.Ir, 34, "discharge_energy_lo", "누적 방전 전력량(Lo)", DType.UInt16, 1, "kWh",
                    "UINT32 하위워드, big-endian, 결합값 ×10 kWh"),
                new RegisterDef(l, ObjType.Ir, 35, "charge_energy_hi", "누적 충전 전력량(Hi)", DType.UInt16, 1, "", "UINT32 상위워드"),
                new RegisterDef(l, ObjType.Ir, 36, "charge_energy_lo", "누적 충전 전력량(Lo)", DType.UInt16, 1, "kWh",
                    "UINT32 하위워드, big-endian, 결합값 ×10 kWh"),
            };
            return new LinkMap(l, "RTDS", "EMS", regs);
        }

        // ── 링크③ 시뮬매니저(서버)–RTDS(클라) — 시트3 ────────────────────
        private static LinkMap BuildLink3()
        {
            var l = Link.Link3;
            var regs = new List<RegisterDef>
            {
                // Input Registers (FC04) — 매니저 보유 / RTDS 읽기
                new RegisterDef(l, ObjType.Ir, 0, "accel_factor", "시간 가속 배율", DType.UInt16, 1, "s", "1~900"),
                new RegisterDef(l, ObjType.Ir, 1, "interval_index", "현재 15분 구간 인덱스", DType.UInt16, 1, "", "0~95"),
                new RegisterDef(l, ObjType.Ir, 2, "scenario_id", "시나리오 ID", DType.UInt16, 1, "", "0~65535"),
                new RegisterDef(l, ObjType.Ir, 3, "ems_status", "EMS 상태", DType.UInt16, 1, "",
                    "0해제/1연결 — 0이면 RTDS는 EMS 지령 무시·안전 유지"),
                new RegisterDef(l, ObjType.Ir, 4, "irradiance", "일사량", DType.UInt16, 1, "MJ/m²",
                    "0~1500, PV 모델 입력 — v1.0에서 W/m²→MJ/m² 변경"),
                new RegisterDef(l, ObjType.Ir, 5, "ambient_temp", "외기온도", DType.Int16, 100, "℃",
                    "±327.67 — 링크①은 ×10(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Ir, 6, "snow_depth", "적설량", DType.UInt16, 100, "cm",
                    "0~655.35 — 링크①은 ×10(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Ir, 7, "weather_code", "기상 상태코드", DType.UInt16, 1, "", "0정상/1폭염/2폭설"),
                new RegisterDef(l, ObjType.Ir, 8, "load_profile_ref", "부하 프로파일 기준값", DType.UInt16, 100, "kW",
                    "0~655.35 — RTDS로만 전달(EMS 블라인드)"),
                new RegisterDef(l, ObjType.Ir, 9, "pv_power", "PV 발전량", DType.UInt16, 100, "kW",
                    "0~655.35 — (옵션) 모델 미사용 시 직접 주입, 검증용"),
                new RegisterDef(l, ObjType.Ir, 10, "load_power", "부하량", DType.UInt16, 100, "kW", "0~655.35 — (옵션) 검증용 참조"),
                new RegisterDef(l, ObjType.Ir, 11, "initial_soc", "초기 SOC 값", DType.UInt16, 100, "%",
                    "0~100, 기동 시 RTDS 주입"),
                // Discrete Inputs (FC02) — 매니저 보유 / RTDS 읽기 (지령 비트)
                new RegisterDef(l, ObjType.Di, 0, "initial_soc_trigger", "초기 SOC 적용 트리거", DType.Bit,
                    note: "1일 때 RTDS가 IR 300011 주입 — 최초 기동에만 세트"),
                new RegisterDef(l, ObjType.Di, 1, "ess_run_enable_sim", "ESS 운전 Enable(시뮬)", DType.Bit,
                    note: "시뮬 차원 허용, EMS Enable과 AND"),
                new RegisterDef(l, ObjType.Di, 2, "reset", "리셋", DType.Bit, note: "Run 전환 시 모델 상태 초기화"),
                // Holding Registers (FC06/16) — RTDS → 매니저 쓰기 (HB·결과 이력)
                new RegisterDef(l, ObjType.Hr, 0, "rtds_heartbeat", "RTDS Heartbeat", DType.UInt16, 1, "", "실 1s 증가, 생존감시"),
                new RegisterDef(l, ObjType.Hr, 1, "rtds_status_code", "RTDS 상태코드", DType.UInt16, 1, "", "0정상/1주의/2고장"),
                new RegisterDef(l, ObjType.Hr, 2, "ess_power", "ESS 충방전 전력", DType.Int16, 10, "kW",
                    "±3276.7, Historian — 링크②는 ×100(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Hr, 3, "ess_soc", "ESS SOC", DType.UInt16, 100, "%", "0~100, 보존 SOC 갱신원"),
                new RegisterDef(l, ObjType.Hr, 4, "pcc_import", "PCC 수전전력", DType.UInt16, 10, "kW",
                    "0~6553.5, 요금 산정 — 링크②는 ×100(스케일 상이 주의)"),
                new RegisterDef(l, ObjType.Hr, 5, "pcc_export", "PCC 역송전력", DType.UInt16, 10, "kW", "0~6553.5"),
                // Coils (FC05/15) — RTDS → 매니저 쓰기 (동작·상태 비트)
                new RegisterDef(l, ObjType.Coil, 0, "rtds_running", "RTDS 운전중", DType.Bit,
                    note: "★연결중개 출발점: 매니저 감지 → ①Coil 000004 세트"),
                new RegisterDef(l, ObjType.Coil, 1, "initial_soc_ack", "초기 SOC 반영완료", DType.Bit,
                    note: "SOC 주입 ack — 매니저 확인 후 트리거 해제"),
            };
            return new LinkMap(l, "시뮬매니저", "RTDS", regs);
        }
    }
}
