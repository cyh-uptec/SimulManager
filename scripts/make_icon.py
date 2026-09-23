"""로고 → 멀티사이즈 ICO·favicon·웹 로고 생성 (apply_logo.bat에서 호출).

입력:  assets/logo.png       마크(아이콘)용 — 권장 512×512 이상, 투명배경
       assets/logo_wide.png  와이드(회사명 포함) 로고 — 메인 화면 표시용 (선택)
출력:  assets/app.ico                  — 16·24·32·48·64·102·128·256 멀티사이즈 (바로가기·exe용)
       frontend/public/favicon.ico    — 16·32·48 (브라우저 탭)
       frontend/public/logo.png       — 128px (사이드바 마크)
       frontend/public/logo_wide.png  — 메인 화면 상단 로고 (원본 복사)
"""
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logo.png"
LOGO_WIDE = ROOT / "assets" / "logo_wide.png"
APP_ICO = ROOT / "assets" / "app.ico"
PUBLIC = ROOT / "frontend" / "public"

# 102px = 실행 아이콘 요청 크기, 나머지는 Windows 표준 크기(선명한 표시용)
ICO_SIZES = [16, 24, 32, 48, 64, 102, 128, 256]
FAVICON_SIZES = [16, 32, 48]


def main() -> None:
    if not LOGO.exists():
        raise SystemExit(f"[오류] 로고 파일이 없습니다: {LOGO}\n"
                         "회사 로고를 assets/logo.png 로 저장한 뒤 다시 실행하세요.")
    img = Image.open(LOGO).convert("RGBA")

    # 정사각 캔버스로 패딩 (비율 유지)
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))

    # 원본보다 큰 크기는 제외 (업스케일 흐림 방지) — Windows가 필요 시 자체 확대
    sizes = [s for s in ICO_SIZES if s <= side] or [side]
    canvas.save(APP_ICO, sizes=[(s, s) for s in sizes])
    print(f"생성: {APP_ICO} ({'·'.join(map(str, sizes))}px)")

    PUBLIC.mkdir(parents=True, exist_ok=True)
    canvas.save(PUBLIC / "favicon.ico", sizes=[(s, s) for s in FAVICON_SIZES])
    canvas.resize((128, 128), Image.LANCZOS).save(PUBLIC / "logo.png")
    print(f"생성: {PUBLIC / 'favicon.ico'}, {PUBLIC / 'logo.png'}")

    if LOGO_WIDE.exists():
        shutil.copyfile(LOGO_WIDE, PUBLIC / "logo_wide.png")
        print(f"생성: {PUBLIC / 'logo_wide.png'} (메인 화면 로고)")
    else:
        print("참고: assets/logo_wide.png 없음 — 메인 화면 로고 생략")


if __name__ == "__main__":
    main()
