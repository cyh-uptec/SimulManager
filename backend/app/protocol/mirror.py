"""링크① DI/IR 쓰기용 HR 미러 주소 — docs/OPEN_ISSUES.md #2.

Modbus 규격상 Client(EMS)는 DI/IR에 쓸 수 없으므로, EMS는 합의된 미러 HR 주소에
FC16으로 기록하고 매니저가 내부적으로 DI/IR 뱅크에 매핑한다 (시퀀스 설계서 §8 옵션 b).

기본값(협의 대상 — EMS측 합의 후 확정):
  DI n  ↔  HR (10000 + n)   참조 410000대  (예: EMS 준비완료 DI 100000 → HR 410000)
  IR n  ↔  HR (30000 + n)   참조 430000대  (예: EMS Heartbeat IR 300000 → HR 430000)

제약 (EMS측 합의문에 포함할 것): 한 번의 FC16 요청은 한 미러 영역(DI대 또는 IR대) 안에
완전히 포함되어야 한다 — 정의된 레지스터 범위 초과·영역 경계 걸침 쓰기는 매핑되지 않는다.
"""
MIRROR_HR_BASE_DI = 10000
MIRROR_HR_BASE_IR = 30000
