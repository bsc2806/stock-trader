"""한국투자증권(KIS) REST API 클라이언트.

Phase 1에서는 '읽기 전용'만 사용합니다:
  - 접근토큰 발급(+캐시)
  - 현재가 조회 (연결 확인용)
주문(매수/매도)은 Phase 2에서 추가합니다. 실수로도 주문이 나가지 않도록
이 파일에는 주문 함수를 아직 넣지 않았습니다.
"""
import json
import time
from pathlib import Path

import requests

from config import (
    KIS_BASE_URL,
    KIS_APP_KEY,
    KIS_APP_SECRET,
    KIS_ENV,
    KIS_ACCOUNT_NO,
    DATA_DIR,
)

_TOKEN_PATH: Path = DATA_DIR / "token_cache.json"


def _get_access_token() -> str:
    """토큰을 캐시에서 재사용. 만료 임박/없으면 새로 발급.

    KIS는 토큰 발급에 호출 제한이 있어 캐싱이 중요합니다.
    """
    if _TOKEN_PATH.exists():
        cached = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
        if cached.get("expires_at", 0) > time.time() + 300:  # 5분 여유
            return cached["access_token"]

    url = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    token = data["access_token"]
    # expires_in(초) 만큼 유효. 보수적으로 저장.
    expires_at = time.time() + int(data.get("expires_in", 86400))
    _TOKEN_PATH.write_text(
        json.dumps({"access_token": token, "expires_at": expires_at}),
        encoding="utf-8",
    )
    return token


def _get_with_retry(url: str, headers: dict, params: dict, tries: int = 3):
    """KIS GET 요청. 5xx/타임아웃/연결오류는 짧은 백오프로 자동 재시도.

    KIS 서버는 종목/타이밍에 따라 간헐적 500을 내는데, 대부분 재시도로 해결됨.
    """
    last_err: Exception | None = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code >= 500:
                last_err = requests.HTTPError(f"{r.status_code} Server Error")
                time.sleep(0.4 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            time.sleep(0.4 * (i + 1))
    raise last_err if last_err else RuntimeError("요청 실패")


def _headers(tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {_get_access_token()}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
    }


def get_current_price(stock_code: str) -> dict:
    """종목 현재가 조회. 모의/실전 도메인 모두 동일 tr_id(FHKST01010100).

    반환: {price: int, change_rate: float, name: str} (핵심 필드만 추림)
    """
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": stock_code}
    resp = _get_with_retry(url, _headers("FHKST01010100"), params)
    out = resp.json().get("output", {})
    return {
        "price": int(out.get("stck_prpr", 0) or 0),
        "change_rate": float(out.get("prdy_ctrt", 0) or 0),
        "name": out.get("bstp_kor_isnm", ""),
    }


def _split_account() -> tuple[str, str]:
    """'50123456-01' → ('50123456', '01'). 대시(-) 없으면 앞 8자리/뒤 2자리."""
    acc = KIS_ACCOUNT_NO.replace(" ", "")
    if "-" in acc:
        cano, prdt = acc.split("-", 1)
    else:
        cano, prdt = acc[:8], acc[8:] or "01"
    return cano, prdt


def get_balance() -> dict:
    """주식 잔고 조회 → 보유 종목 목록 + 손익 요약.

    반환:
      {
        "holdings": [ {code,name,qty,avg_price,price,eval_amt,pnl_amt,pnl_rate} ],
        "summary": {total_eval, buy_amount, pnl_amount, pnl_rate, cash, net_asset}
      }
    """
    cano, prdt = _split_account()
    tr_id = "VTTC8434R" if KIS_ENV == "vts" else "TTTC8434R"
    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    resp = _get_with_retry(url, _headers(tr_id), params)
    data = resp.json()

    holdings = []
    for row in data.get("output1", []):
        qty = int(row.get("hldg_qty", 0) or 0)
        if qty <= 0:
            continue
        holdings.append({
            "code": row.get("pdno", ""),
            "name": row.get("prdt_name", ""),
            "qty": qty,
            "avg_price": int(float(row.get("pchs_avg_pric", 0) or 0)),
            "price": int(row.get("prpr", 0) or 0),
            "eval_amt": int(row.get("evlu_amt", 0) or 0),
            "pnl_amt": int(row.get("evlu_pfls_amt", 0) or 0),
            "pnl_rate": float(row.get("evlu_pfls_rt", 0) or 0),
        })

    o2 = (data.get("output2") or [{}])[0]
    # 예수금은 D+2 정산 반영액(가수도정산금액)을 사용. 매도대금이 D+2에 들어오므로
    # 원시 예수금(dnca_tot_amt)은 정산 전이라 음수로 보일 수 있음 → 정산액이 실제 가용 현금.
    settled = o2.get("prvs_rcdl_excc_amt")
    cash = int(settled) if settled not in (None, "") else int(o2.get("dnca_tot_amt", 0) or 0)
    summary = {
        "total_eval": int(o2.get("tot_evlu_amt", 0) or 0),
        "buy_amount": int(o2.get("pchs_amt_smtl_amt", 0) or 0),
        "pnl_amount": int(o2.get("evlu_pfls_smtl_amt", 0) or 0),
        "cash": cash,                                            # D+2 정산 반영(실제 가용)
        "cash_raw": int(o2.get("dnca_tot_amt", 0) or 0),         # 정산 전 원시 예수금
        "net_asset": int(o2.get("nass_amt", 0) or 0),
    }
    buy = summary["buy_amount"]
    summary["pnl_rate"] = round(summary["pnl_amount"] / buy * 100, 2) if buy else 0.0
    return {"holdings": holdings, "summary": summary}


def _hashkey(body: dict) -> str:
    """주문 바디 위변조 방지용 해시키 발급."""
    url = f"{KIS_BASE_URL}/uapi/hashkey"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
    }
    r = requests.post(url, headers=headers, json=body, timeout=10)
    r.raise_for_status()
    return r.json()["HASH"]


def place_order(code: str, side: str, qty: int, price: int = 0, market: bool = True) -> dict:
    """현금 주문 (모의/실전 도메인 자동). side: 'BUY' | 'SELL'.

    market=True → 시장가(ORD_DVSN=01, 단가 0). 자동 재시도하지 않음(중복주문 방지).
    반환: {ok, msg, order_no, raw}
    """
    cano, prdt = _split_account()
    if side == "BUY":
        tr_id = "VTTC0802U" if KIS_ENV == "vts" else "TTTC0802U"
    elif side == "SELL":
        tr_id = "VTTC0801U" if KIS_ENV == "vts" else "TTTC0801U"
    else:
        raise ValueError("side must be BUY or SELL")

    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "PDNO": code,
        "ORD_DVSN": "01" if market else "00",
        "ORD_QTY": str(int(qty)),
        "ORD_UNPR": "0" if market else str(int(price)),
    }
    headers = _headers(tr_id)
    headers["custtype"] = "P"  # 개인
    try:
        headers["hashkey"] = _hashkey(body)
    except Exception:
        pass  # 해시키 실패해도 주문은 시도 (일부 환경 선택적)

    url = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    r = requests.post(url, headers=headers, json=body, timeout=15)  # 재시도 없음
    try:
        data = r.json()
    except Exception:
        return {"ok": False, "msg": f"HTTP {r.status_code}", "order_no": "", "raw": r.text[:200]}

    ok = str(data.get("rt_cd")) == "0"
    return {
        "ok": ok,
        "msg": data.get("msg1", "").strip(),
        "order_no": (data.get("output") or {}).get("ODNO", ""),
        "raw": data,
    }
