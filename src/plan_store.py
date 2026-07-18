"""AI 매수 계획 공유 저장소 + 호출 주기 제한 (크레딧 절약).

서버·트레이더가 같은 data/plan.json 을 공유한다.
- AI 종목 선정(select_portfolio)은 '결정 주기'마다 한 번만 호출 (예: 4시간).
- 그 사이엔 저장된 계획을 재사용 → 두 프로세스가 함께 돌아도 AI 호출은 주기당 1회로 수렴.
보호 규칙(손절/익절/상폐/낙폭)은 AI 호출이 아니라 규칙 계산이라 자주 돌려도 무료.
"""
import json
import time
from pathlib import Path

PLAN_PATH = Path(__file__).resolve().parent.parent / "data" / "plan.json"


def read() -> dict:
    try:
        return json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"ts": 0.0, "picks": []}


def write(picks: list) -> None:
    try:
        PLAN_PATH.write_text(
            json.dumps({"ts": time.time(), "picks": picks}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def is_due(interval_min: int) -> bool:
    """마지막 AI 결정 이후 interval_min 분이 지났으면 True → 새로 호출해도 됨."""
    return (time.time() - read().get("ts", 0.0)) >= interval_min * 60


def age_min() -> int | None:
    ts = read().get("ts", 0.0)
    return int((time.time() - ts) / 60) if ts else None
