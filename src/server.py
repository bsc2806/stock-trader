"""실시간 자율 트레이더 대시보드 웹서버 (Phase 1~2 관측, 읽기 전용).

  python src/server.py  →  http://localhost:5000

- 잔고/손익: 요청 때마다 KIS 실시간 조회 (무료)
- AI 두뇌(시장 스캔 + 종목 선정): 백그라운드 스레드가 주기적으로 갱신 후 캐시
  → 대시보드 새로고침은 AI를 호출하지 않음 (크레딧 절약)
- 주문 실행 기능 없음 (관측 전용)
"""
import time
import json
import threading
import datetime as dt
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

import config
import kis_client
import scanner
import risk
import analyzer
import market
import plan_store

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
STATE_PATH = ROOT / "data" / "state.json"
TRADES_PATH = ROOT / "data" / "trades.json"
POSITIONS_PATH = ROOT / "data" / "positions.json"
LOG_PATH = ROOT / "data" / "trader_log.json"

app = Flask(__name__)
rm = risk.RiskManager()

# AI 두뇌 갱신 주기. 실제 절감은 analyzer 공시 캐시가 담당(새 공시 없으면 호출 0).
BRAIN_TTL = 1800  # 30분

_lock = threading.Lock()
_brain: dict = {"candidates": [], "plan": [], "holdings_actions": [],
                "circuit": False, "dd_level": "NORMAL", "dd_pct": 0.0, "ts": 0.0}
_ai_available: bool | None = None
_balance_cache: dict | None = None


# ---------- 유틸 ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _update_peak(net: int) -> int:
    """고점 순자산 추적(서킷브레이커용). data/state.json 에 저장."""
    st = _read_json(STATE_PATH, {})
    peak = max(int(st.get("peak_net", 0)), net)
    st["peak_net"] = peak
    try:
        STATE_PATH.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass
    return peak


def _fetch_balance() -> dict | None:
    global _balance_cache
    try:
        b = kis_client.get_balance()
        _balance_cache = b
        return b
    except Exception:
        return _balance_cache  # 실패 시 마지막 정상값


# ---------- AI 두뇌 ----------
def _refresh_brain() -> None:
    global _ai_available
    bal = _fetch_balance()
    cands = scanner.scan_candidates(days=2)

    net = cash = equity = 0
    holdings: list[dict] = []
    actions: list[dict] = []
    positions = _read_json(POSITIONS_PATH, {})
    pos_changed = False
    if bal:
        s = bal["summary"]
        net = s.get("net_asset") or s.get("total_eval") or 0
        cash = s.get("cash", 0)
        holdings = bal.get("holdings", [])
        equity = max(0, net - cash)
        for h in holdings:
            pos = positions.get(h["code"], {})
            hz = pos.get("horizon")
            peak = max(float(pos.get("peak", 0.0)), h["pnl_rate"])  # 최고 수익률 갱신
            if peak != pos.get("peak"):
                pos["peak"] = peak
                positions[h["code"]] = pos
                pos_changed = True
            actions.append({**h, "horizon": hz, "peak": round(peak, 2),
                            "action": rm.exit_decision(h["pnl_rate"], peak, hz)})
    if pos_changed:
        try:
            POSITIONS_PATH.write_text(json.dumps(positions, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    peak = _update_peak(net) if net else 0
    dd_pct = round(rm.drawdown_pct(net, peak), 1) if net else 0.0
    level = rm.drawdown_level(net, peak) if net else "NORMAL"

    plan: list[dict] = []
    if net and level == "NORMAL":  # 낙폭 방어 단계면 신규 매수 계획 없음
        slots = rm.slots_available(len(holdings))
        budget = rm.buy_budget(net, equity, cash)
        per_max = rm.max_per_position(net)
        if slots > 0 and budget >= rm.r["min_order_amount"]:
            # AI 호출은 결정 주기마다 1회만 + 정규장에만. 그 외엔 저장된 계획 재사용(무료).
            if market.is_open() and plan_store.is_due(config.AI_DECISION_INTERVAL_MIN):
                try:
                    picks = analyzer.select_portfolio(cands, cash=budget,
                                                      holdings=holdings, max_slots=slots,
                                                      aggressive=(config.KIS_ENV == "vts"))
                    _ai_available = True
                    plan_store.write(picks)
                except Exception as e:
                    msg = str(e).lower()
                    if "credit" in msg or "authentication" in msg or "api_key" in msg:
                        _ai_available = False
                    picks = plan_store.read().get("picks", [])
            else:
                picks = plan_store.read().get("picks", [])
            held_codes = {h["code"] for h in holdings}
            picks = [p for p in picks if p.get("code") not in held_codes][:slots]
            each = min(per_max, budget // len(picks)) if picks else 0
            for p in picks:
                if each >= rm.r["min_order_amount"]:
                    plan.append({**p, "amount": each})

    with _lock:
        _brain.update(candidates=cands[:20], plan=plan, holdings_actions=actions,
                      circuit=(level == "LIQUIDATE"), dd_level=level, dd_pct=dd_pct,
                      ts=time.time())


def _brain_loop() -> None:
    while True:
        try:
            _refresh_brain()
        except Exception as e:
            print("[brain refresh 오류]", e)
        time.sleep(BRAIN_TTL)


# ---------- API ----------
@app.route("/api/state")
def api_state():
    bal = _fetch_balance()
    balance = {"ok": False, "holdings": [], "summary": {}}
    if bal:
        balance.update(ok=True, holdings=bal.get("holdings", []),
                       summary=bal.get("summary", {}))

    with _lock:
        brain = json.loads(json.dumps(_brain))  # 얕은 복사
    trades = _read_json(TRADES_PATH, [])

    return jsonify({
        "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "kis_env": config.KIS_ENV,
        "market": market.status(),
        "ai_available": _ai_available,
        "brain_age_sec": int(time.time() - brain["ts"]) if brain["ts"] else None,
        "rules": rm.r,
        "balance": balance,
        "candidates": brain["candidates"],
        "plan": brain["plan"],
        "holdings_actions": brain["holdings_actions"],
        "circuit": brain["circuit"],
        "dd_level": brain["dd_level"],
        "dd_pct": brain["dd_pct"],
        "trades": trades,
        "logs": _read_json(LOG_PATH, []),
    })


@app.route("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


def main() -> None:
    config.require("KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY", "KIS_ACCOUNT_NO")
    threading.Thread(target=_brain_loop, daemon=True).start()
    print("대시보드: http://localhost:5000  (Ctrl+C 종료)")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
