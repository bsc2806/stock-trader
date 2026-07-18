"""환경변수(.env) 로딩 및 검증. 키는 코드에 하드코딩하지 않고 여기서만 읽습니다."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KIS_ENV = os.getenv("KIS_ENV", "vts").strip()
KIS_APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "").strip()
DART_API_KEY = os.getenv("DART_API_KEY", "").strip()

# 실전 자동매매 안전 플래그. 기본 False → 모의(vts)에서만 주문 실행.
# 실전 주문은 KIS_ENV=prod 이면서 LIVE_TRADING=true 를 명시해야만 허용.
TRADING_LIVE = os.getenv("LIVE_TRADING", "false").strip().lower() == "true"

# AI 종목 선정 호출 주기(분). 이 간격마다 최대 1회만 AI 호출 → 크레딧 절약.
# 기본 240분(4시간, 정규장 기준 하루 약 2회). 하루 1회 원하면 1440.
AI_DECISION_INTERVAL_MIN = int(os.getenv("AI_DECISION_INTERVAL_MIN", "240"))

# KIS 도메인: 모의(vts) / 실전(prod)
KIS_BASE_URL = (
    "https://openapivts.koreainvestment.com:29443"
    if KIS_ENV == "vts"
    else "https://openapi.koreainvestment.com:9443"
)

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)


def require(*keys: str) -> None:
    """필수 키가 비어있으면 친절하게 안내하고 종료."""
    missing = [k for k in keys if not globals().get(k)]
    if missing:
        print("[설정 오류] .env 에 다음 값이 비어있습니다:", ", ".join(missing))
        print("  → .env.example 을 복사해 .env 를 만들고 값을 채워주세요.")
        sys.exit(1)
