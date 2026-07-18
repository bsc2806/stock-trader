"""AI(Claude) 기반 공시 분석.

LLM이 '매수/매도'를 직접 실행하지 않습니다. 여기서는 공시를 읽고
신호(signal)와 근거(reason)만 냅니다. 실제 주문은 Phase 2에서
리스크 규칙(손절/한도 등)으로 감싼 뒤에만 이뤄집니다.
"""
import json
from pathlib import Path

import anthropic

from config import DATA_DIR

MODEL = "claude-opus-4-8"

# --- 공시 분석 캐시 (rcept_no 기준). 같은 공시 재분석 금지 → 크레딧 절약 ---
_CACHE_PATH: Path = DATA_DIR / "ai_cache.json"
try:
    _ai_cache: dict = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
except Exception:
    _ai_cache = {}


def _cache_key(d: dict) -> str:
    return d.get("rcept_no") or f"{d.get('rcept_dt', '')}|{d.get('report_nm', '')}"


def _save_cache() -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(_ai_cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

# 구조화 출력 스키마: 신호/신뢰도/근거만 받는다.
_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["BUY", "SELL", "HOLD", "WATCH"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["signal", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "당신은 한국 주식 공시 분석가입니다. 주어진 공시 제목/유형을 보고 "
    "해당 종목에 대한 투자 신호를 판단하세요. 공시가 실적 개선, 대형 수주, "
    "자사주 매입 등 호재면 BUY 쪽, 유상증자·악재·소송 등이면 SELL/WATCH, "
    "영향이 불분명하면 HOLD 또는 WATCH로 판단합니다. 근거(reason)는 한국어로 "
    "1~2문장, 간결하게. 확신이 없으면 confidence를 낮추세요. "
    "'카더라'식 추측은 배제하고 공시 팩트에만 근거하세요."
)

# 저신호(노이즈) 공시: 정기 신고성이라 매매 판단엔 거의 무의미
_NOISE_KEYWORDS = ("소유상황보고서", "특정증권등소유")

# 상장폐지/관리종목 위험 신호: 매수 배제 + 보유 시 손실 최소화 매도 대상
DELISTING_KEYWORDS = (
    "상장적격성", "상장폐지", "관리종목", "매매거래정지", "거래정지",
    "투자주의환기", "불성실공시법인", "자본잠식", "회생절차", "파산",
    "감사의견거절", "감사의견부적정", "감사의견한정", "횡령", "배임",
    "상장폐지사유", "형식적상장폐지",
)


# 공시 유형으로 장기/단기 성향 힌트 (무비용 휴리스틱, 스캔 단계용)
_LONG_KW = ("실적", "결산", "증설", "시설투자", "합병", "자기주식취득", "자기주식소각",
            "인수", "영업양수", "출자")
_SHORT_KW = ("공급계약", "단일판매", "수주", "기업설명회", "IR", "배당", "자기주식처분")


def horizon_hint(disclosures: list[dict]) -> str:
    """공시 유형 기반 장기/단기 성향 힌트: '장기' | '단기' | '혼재'."""
    text = " ".join(d.get("report_nm") or d.get("title") or "" for d in disclosures)
    long_s = sum(1 for kw in _LONG_KW if kw in text)
    short_s = sum(1 for kw in _SHORT_KW if kw in text)
    if long_s > short_s:
        return "장기"
    if short_s > long_s:
        return "단기"
    return "혼재"


def delisting_risk(disclosures: list[dict]) -> dict:
    """공시 목록에 상장폐지/관리종목 위험 신호가 있으면 {risk:True, reason}."""
    for d in disclosures:
        title = d.get("report_nm") or d.get("title") or ""
        for kw in DELISTING_KEYWORDS:
            if kw in title:
                return {"risk": True, "reason": f"{kw} 관련 공시"}
    return {"risk": False, "reason": ""}

_client = None


def _get_client() -> "anthropic.Anthropic":
    """Anthropic 클라이언트 지연 초기화. (키 없을 때 import 시점에 죽지 않도록)"""
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def analyze_disclosure(stock_name: str, disclosure: dict) -> dict:
    """공시 1건을 분석해 {signal, confidence, reason} 반환. (캐시 적용)"""
    key = _cache_key(disclosure)
    if key in _ai_cache:
        return dict(_ai_cache[key])  # 이미 분석한 공시 → API 호출 없이 캐시 반환

    report_nm = disclosure.get("report_nm", "")
    rcept_dt = disclosure.get("rcept_dt", "")

    user_msg = (
        f"종목: {stock_name}\n"
        f"공시일: {rcept_dt}\n"
        f"공시제목: {report_nm}\n\n"
        "이 공시를 근거로 투자 신호를 판단하세요."
    )

    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": user_msg}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    result = json.loads(text)
    _ai_cache[key] = result   # 캐시에 저장 → 같은 공시 재분석 방지
    _save_cache()
    return result


_PORTFOLIO_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "conviction": {"type": "string", "enum": ["high", "medium", "low"]},
                    "horizon": {"type": "string", "enum": ["장기", "단기"]},
                    "holding_period": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["code", "name", "conviction", "horizon",
                             "holding_period", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["picks"],
    "additionalProperties": False,
}

# 공통: 장기/단기 분류 + 보유기간 명시 지침
_HORIZON_GUIDE = (
    "각 종목을 반드시 '장기' 또는 '단기'로 분류하고 holding_period에 구체적 예상 보유기간을 쓰세요. "
    "장기: 실적·구조적 성장 근거 → 수개월~수년(예 '6개월~1년', '2~3년'). "
    "단기: 공시 이벤트·수급 모멘텀 → 당일~며칠(예 '당일', '2~3일', '1주일'). "
    "reason에는 그 기간으로 본 근거(어떤 공시를 며칠/몇 개월 관점에서 봤는지)를 포함하세요."
)

_PORTFOLIO_SYSTEM = (
    "당신은 한국 주식 자율 트레이더입니다. 발굴된 공시 후보 종목들 중에서 "
    "매수할 종목을 선별하세요. 원칙: (1) 원금 보존이 최우선 — 확신 없으면 고르지 말 것. "
    "(2) 유동성 큰 대형주(코스피)를 우선. (3) 공시가 실질 호재(대형 수주·실적 개선·자사주 매입 등)인 "
    "종목만. 유상증자·악재성은 제외. (4) 요청된 최대 종목 수를 넘기지 말 것. "
    "(5) ★상장폐지·관리종목 위험 회피★ — 자본잠식, 상장적격성 실질심사, 감사의견 문제, "
    "횡령·배임, 거래정지 등 상장폐지로 이어질 수 있는 조짐이 조금이라도 보이면 절대 매수 금지. "
    "확신 가는 게 없으면 picks를 빈 배열로. " + _HORIZON_GUIDE +
    " 수량/금액은 판단하지 말 것(리스크 모듈이 계산)."
)

_PORTFOLIO_SYSTEM_AGGRESSIVE = (
    "당신은 한국 주식 자율 트레이더입니다(모의투자·적극 모드). 발굴된 공시 후보 중 매수할 종목을 "
    "선별하세요. 지금은 모의계좌라 다양하게 적극적으로 시도합니다. 원칙: "
    "(1) 명백한 위험만 배제하고 폭넓게 시도 — 저확신도 실험적으로 담아도 됨. "
    "(2) ★상장폐지·관리종목 위험은 반드시 회피★ — 자본잠식, 상장적격성 실질심사, 감사의견 문제, "
    "횡령·배임, 거래정지 등 상폐로 이어질 수 있는 종목은 절대 매수 금지. "
    "(3) 유상증자 등 명백한 악재도 제외. (4) 실질 호재를 우선하되 폭넓게, 최대 종목 수까지 적극 활용. "
    + _HORIZON_GUIDE + " 수량/금액은 판단하지 말 것(리스크 모듈이 계산)."
)


def select_portfolio(candidates: list[dict], cash: int, holdings: list[dict],
                     max_slots: int, aggressive: bool = False) -> list[dict]:
    """후보 종목 중 매수할 종목을 AI가 선별.

    반환: [{code,name,conviction,horizon,holding_period,reason}]
    aggressive=True(모의): 명백한 위험만 빼고 폭넓게. False(실전): 보수적.
    """
    if not candidates or max_slots <= 0:
        return []

    cand_lines = []
    for c in candidates:
        titles = "; ".join(d["title"] for d in c.get("disclosures", [])[:3])
        cand_lines.append(f"- [{c['code']}] {c['name']} (공시: {titles})")
    held = ", ".join(f"{h['name']}({h['code']})" for h in holdings) or "없음"

    user_msg = (
        f"현재 보유 종목: {held}\n"
        f"매수 가능 현금: {cash:,}원\n"
        f"추가 편입 가능 종목 수: 최대 {max_slots}개\n\n"
        f"발굴된 공시 후보:\n" + "\n".join(cand_lines) + "\n\n"
        "위 후보 중 지금 매수할 종목을 선별하고, 각 종목의 장기/단기와 예상 보유기간을 명시하세요."
    )

    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=3072,
        system=_PORTFOLIO_SYSTEM_AGGRESSIVE if aggressive else _PORTFOLIO_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _PORTFOLIO_SCHEMA}},
        messages=[{"role": "user", "content": user_msg}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    picks = json.loads(text).get("picks", [])
    return picks[:max_slots]


def is_signal_worthy(disclosure: dict) -> bool:
    """저신호 공시(소유상황보고서 등) 제외."""
    title = disclosure.get("report_nm", "")
    return not any(kw in title for kw in _NOISE_KEYWORDS)


def analyze_stock(stock_name: str, disclosures: list[dict]) -> dict:
    """종목의 공시들 중 '신호성' 공시를 골라 대표 신호 1건 생성.

    반환: {signal, confidence, reason, based_on, has_ai}
    - 신호성 공시가 없으면 HOLD/관망(AI 호출 없이).
    """
    worthy = [d for d in disclosures if is_signal_worthy(d)]
    if not worthy:
        return {
            "signal": "HOLD",
            "confidence": "low",
            "reason": "최근 특이 공시 없음 (정기 신고성 공시만 존재).",
            "based_on": "",
            "has_ai": False,
        }

    # 가장 최근 신호성 공시 1건으로 판단
    top = sorted(worthy, key=lambda d: d.get("rcept_dt", ""), reverse=True)[0]
    result = analyze_disclosure(stock_name, top)
    result["based_on"] = f"{top.get('rcept_dt')} {top.get('report_nm')}"
    result["has_ai"] = True
    return result
