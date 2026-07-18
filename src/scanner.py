"""시장 스캔(종목 발굴) 엔진.

고정 관심종목 대신, 전체 시장의 최근 공시를 훑어 '신호성' 종목을 발굴한다.
DART list.json은 항목마다 stock_code를 주므로, 상장 종목만 추려 그룹핑한다.

기본은 코스피(유가증권, corp_cls=Y) 대형주 위주. 성과가 쌓이면 코스닥(K)까지 확대.
"""
import datetime as dt

import requests

from config import DART_API_KEY
import analyzer

BASE = "https://opendart.fss.or.kr/api"

# 공시유형: B=주요사항보고(증자·자기주식·합병 등 고신호), I=거래소공시(공급계약·실적·IR 등)
DEFAULT_TYPES = ("B", "I")


def _fetch_type(pblntf_ty: str, bgn: str, end: str, corp_cls: str, max_pages: int = 5) -> list[dict]:
    items: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": bgn,
            "end_de": end,
            "pblntf_ty": pblntf_ty,
            "corp_cls": corp_cls,
            "page_no": str(page),
            "page_count": "100",
        }
        try:
            r = requests.get(f"{BASE}/list.json", params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception:
            break
        if data.get("status") not in ("000", "013"):
            break
        items.extend(data.get("list", []))
        if page >= int(data.get("total_page", 1)):
            break
    return items


def scan_candidates(days: int = 2, corp_cls: str = "Y",
                    types: tuple[str, ...] = DEFAULT_TYPES,
                    max_candidates: int = 25) -> list[dict]:
    """최근 `days`일 시장 공시에서 신호성 종목 후보를 발굴.

    반환: [{code, name, corp_cls, disclosures:[{date,title}], score}] (score 내림차순)
    """
    today = dt.date.today()
    bgn = (today - dt.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    raw: list[dict] = []
    for t in types:
        raw.extend(_fetch_type(t, bgn, end, corp_cls))

    by_code: dict[str, dict] = {}
    for it in raw:
        code = (it.get("stock_code") or "").strip()
        if not code or code == " ":
            continue  # 비상장 제외
        if not analyzer.is_signal_worthy(it):
            continue  # 소유상황보고서 등 노이즈 제외
        entry = by_code.setdefault(code, {
            "code": code,
            "name": it.get("corp_name", ""),
            "corp_cls": it.get("corp_cls", corp_cls),
            "disclosures": [],
        })
        entry["disclosures"].append({
            "date": it.get("rcept_dt", ""),
            "title": it.get("report_nm", ""),
        })

    # 상장폐지/관리종목 위험 종목은 후보에서 제외 (원금 보존)
    safe = [e for e in by_code.values() if not analyzer.delisting_risk(e["disclosures"])["risk"]]

    # 신호성 공시 건수 + 최신성으로 스코어, 장기/단기 힌트 태그
    for e in safe:
        latest = max((d["date"] for d in e["disclosures"]), default="")
        e["score"] = len(e["disclosures"]) + (0.5 if latest == end else 0)
        e["horizon_hint"] = analyzer.horizon_hint(e["disclosures"])

    ranked = sorted(safe, key=lambda x: x["score"], reverse=True)
    return ranked[:max_candidates]


if __name__ == "__main__":
    import config
    cands = scan_candidates(days=2)
    print(f"발굴된 후보 {len(cands)}개 (코스피, 최근 2일 신호성 공시)")
    for c in cands[:15]:
        titles = ", ".join(d["title"] for d in c["disclosures"][:2])
        print(f"  [{c['code']}] {c['name']} · {len(c['disclosures'])}건 · {titles}")
