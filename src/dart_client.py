"""DART 전자공시 Open API 클라이언트.

- 종목코드(6자리) → DART corp_code 매핑 (corpCode.xml 다운로드, 캐시)
- 특정 기업의 최근 공시 목록 조회
공시는 '팩트' 기반이라 AI 분석의 1차 신호로 적합합니다.
"""
import io
import json
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from config import DART_API_KEY, DATA_DIR

BASE = "https://opendart.fss.or.kr/api"
_CORP_MAP_PATH: Path = DATA_DIR / "corp_codes.json"


def _download_corp_map() -> dict:
    """DART corpCode.xml(zip)을 받아 {종목코드: corp_code} 맵으로 캐시."""
    url = f"{BASE}/corpCode.xml"
    resp = requests.get(url, params={"crtfc_key": DART_API_KEY}, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_bytes)
    mapping: dict[str, str] = {}
    for item in root.iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and stock_code != " " and corp_code:
            mapping[stock_code] = corp_code

    _CORP_MAP_PATH.write_text(json.dumps(mapping), encoding="utf-8")
    return mapping


def get_corp_map(force_refresh: bool = False) -> dict:
    """캐시가 있으면 재사용, 없으면 다운로드. (매일 한 번 갱신 정도면 충분)"""
    if _CORP_MAP_PATH.exists() and not force_refresh:
        return json.loads(_CORP_MAP_PATH.read_text(encoding="utf-8"))
    return _download_corp_map()


def get_recent_disclosures(corp_code: str, days: int = 3) -> list[dict]:
    """corp_code 기업의 최근 `days`일 공시 목록.

    반환 항목 예: {report_nm, rcept_dt, rcept_no, flr_nm}
    (제목/유형 기반 1차 분석용. 전문 파싱은 다음 단계에서 추가)
    """
    today = dt.date.today()
    bgn = (today - dt.timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bgn_de": bgn,
        "end_de": end,
        "page_no": "1",
        "page_count": "50",
    }
    resp = requests.get(f"{BASE}/list.json", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # status "013" = 데이터 없음 (정상), "000" = 정상
    if data.get("status") not in ("000", "013"):
        raise RuntimeError(f"DART 오류: {data.get('status')} {data.get('message')}")

    return data.get("list", [])
