"""AI 자율 매매 오케스트레이터 (모의 전용 기본).

  python src/trader.py          # 1회 실행
  python src/trader.py --loop   # 정규장 동안 주기적 실행

안전 순서:
  ① 실전 차단 가드 (기본 모의 전용)
  ② 장 운영시간 가드 (평일 09:00~15:30 외엔 관측만)
  ③ 서킷브레이커 → 원금 방어선 도달 시 전량 청산 + 매수 중지
  ④ 보유 종목 손절/익절 자동 청산
  ⑤ 당일 손실 한도 → 신규 매수 중지
  ⑥ 시장 스캔 → AI 선정 → 리스크 통과분만 매수

모든 체결 시도는 data/trades.json 에 기록되어 대시보드에 표시됨.
주문은 자동 재시도하지 않음(중복주문 방지).
"""
import sys
import json
import time
import datetime as dt
from pathlib import Path

import config
import kis_client
import scanner
import risk
import analyzer
import market
import dart_client
import plan_store

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "state.json"
TRADES_PATH = ROOT / "data" / "trades.json"
POSITIONS_PATH = ROOT / "data" / "positions.json"  # 종목별 매수 시 horizon(장기/단기) 기록
LOG_PATH = ROOT / "data" / "trader_log.json"        # 실행 로그 (대시보드에서 확인)

LOOP_INTERVAL = 300  # --loop 모드: 5분마다


# ---------- 저장소 ----------
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def log(*args) -> None:
    """콘솔 출력 + 실행 로그 파일에 기록 (대시보드에서 확인)."""
    msg = " ".join(str(a) for a in args)
    sys.stdout.write(msg + "\n")
    try:
        logs = _read_json(LOG_PATH, [])
        logs.insert(0, {"time": dt.datetime.now().strftime("%m-%d %H:%M:%S"), "msg": msg.strip()})
        _write_json(LOG_PATH, logs[:300])
    except Exception:
        pass


def _log_trade(entry: dict) -> None:
    trades = _read_json(TRADES_PATH, [])
    entry["time"] = dt.datetime.now().strftime("%m-%d %H:%M:%S")
    trades.insert(0, entry)  # 최신 우선
    _write_json(TRADES_PATH, trades[:500])


def _record_position(code: str, horizon: str, period: str) -> None:
    p = _read_json(POSITIONS_PATH, {})
    p[code] = {"horizon": horizon, "holding_period": period, "peak": 0.0}
    _write_json(POSITIONS_PATH, p)


def _forget_position(code: str) -> None:
    p = _read_json(POSITIONS_PATH, {})
    if code in p:
        p.pop(code)
        _write_json(POSITIONS_PATH, p)


# ---------- 안전 가드 ----------
def _guard_live() -> None:
    if config.KIS_ENV != "vts" and not config.TRADING_LIVE:
        raise SystemExit(
            "⛔ 실전 주문 차단됨. 실전을 켜려면 KIS_ENV=prod + LIVE_TRADING=true 를 명시하세요. "
            "(기본은 모의 전용 — 검증 전 원금 보호)"
        )


# ---------- 매도 ----------
def _sell(h: dict, reason: str, forget: bool = True) -> None:
    res = kis_client.place_order(h["code"], "SELL", h["qty"], market=True)
    _log_trade({
        "side": "SELL", "code": h["code"], "name": h["name"],
        "qty": h["qty"], "price": h["price"], "amount": h["price"] * h["qty"],
        "reason": reason, "ok": res["ok"], "msg": res["msg"], "order_no": res["order_no"],
    })
    if forget and res["ok"]:
        _forget_position(h["code"])  # 부분정리(forget=False)는 유지
    mark = "✅" if res["ok"] else "❌"
    log(f"    {mark} 매도 {h['name']}({h['code']}) {h['qty']}주 · {reason} · {res['msg']}")


# ---------- 1 사이클 ----------
def run_cycle() -> None:
    _guard_live()
    mk = market.status()
    log(f"[{dt.datetime.now():%H:%M:%S}] {mk['session']} · KIS={config.KIS_ENV}")
    if not mk["open"]:
        log(f"    장 미개장 — 관측만. 다음 장 {mk['next_open']}")
        return

    rm = risk.RiskManager()
    bal = kis_client.get_balance()
    s = bal["summary"]
    net = s.get("net_asset") or s.get("total_eval") or 0
    cash = s.get("cash", 0)
    holdings = bal["holdings"]
    equity = max(0, net - cash)

    # peak / 당일 시작 순자산 추적
    st = _read_json(STATE_PATH, {})
    peak = max(int(st.get("peak_net", 0)), net)
    today = dt.date.today().strftime("%Y%m%d")
    if st.get("day") != today:
        st["day"], st["day_open_net"] = today, net
    day_open = st.get("day_open_net", net)
    st["peak_net"] = peak
    _write_json(STATE_PATH, st)
    day_pnl_pct = (net - day_open) / day_open * 100 if day_open else 0.0

    # 계좌 낙폭 단계 판정 (단계적 방어)
    dd = rm.drawdown_pct(net, peak)
    level = rm.drawdown_level(net, peak)

    # ③ 전량 청산 — 진짜 붕괴에만 (dd_liquidate 이하)
    if level == "LIQUIDATE":
        log(f"    ⛔ 낙폭 {dd:+.1f}% — 전량 청산 (고점 {peak:,} → {net:,})")
        for h in holdings:
            _sell(h, f"전량청산·낙폭 {dd:+.1f}%")
        return

    # ④ 보유 종목: peak 갱신 → 상장폐지 회피 → 손절/트레일링 익절 → (TRIM단계) 손실 절반 정리
    positions = _read_json(POSITIONS_PATH, {})
    corp_map = dart_client.get_corp_map() if holdings else {}
    for h in holdings:
        code = h["code"]
        pos = positions.get(code, {})
        hz = pos.get("horizon")
        peak = max(float(pos.get("peak", 0.0)), h["pnl_rate"])  # 최고 수익률 갱신
        pos["peak"] = peak
        positions[code] = pos

        cc = corp_map.get(code)
        if cc:
            try:
                dr = analyzer.delisting_risk(dart_client.get_recent_disclosures(cc, days=14))
                if dr["risk"]:
                    _sell(h, f"상장폐지 위험 회피 · {dr['reason']}")
                    positions.pop(code, None)
                    continue
            except Exception:
                pass

        act = rm.exit_decision(h["pnl_rate"], peak, hz)
        if act == "STOP":
            _sell(h, f"손절({hz or '기본'}) {h['pnl_rate']:+.2f}%")
            positions.pop(code, None)
            continue
        if act == "TRAIL":
            _sell(h, f"트레일링 익절({hz or '기본'}) 고점{peak:+.1f}%→{h['pnl_rate']:+.2f}%")
            positions.pop(code, None)
            continue
        if act == "TAKE":
            _sell(h, f"상한 익절({hz or '기본'}) {h['pnl_rate']:+.2f}%")
            positions.pop(code, None)
            continue
        if level == "TRIM" and h["pnl_rate"] < 0:  # 부분 방어: 손실 종목 절반 정리
            half = max(1, h["qty"] // 2)
            _sell({**h, "qty": half}, f"부분정리·낙폭 {dd:+.1f}%", forget=False)

    _write_json(POSITIONS_PATH, positions)  # peak 갱신 저장

    # ⑤ 당일 손실 한도 → 신규 매수 중지
    if rm.daily_halt(day_pnl_pct):
        log(f"    ⚠️ 당일 손익 {day_pnl_pct:+.2f}% — 손실 한도 도달, 신규 매수 중지")
        return

    # 낙폭 방어 단계면 신규 매수 중지 (HALT_BUY / TRIM)
    if level != "NORMAL":
        log(f"    🛡️ 낙폭 {dd:+.1f}% ({level}) — 신규 매수 중지")
        return

    # ⑥ 신규 매수 (매도 반영 위해 잔고 재조회)
    bal2 = kis_client.get_balance()
    s2 = bal2["summary"]
    net2 = s2.get("net_asset") or s2.get("total_eval") or 0
    cash2 = s2.get("cash", 0)
    holdings2 = bal2["holdings"]
    equity2 = max(0, net2 - cash2)
    held = {h["code"] for h in holdings2}

    budget = rm.buy_budget(net2, equity2, cash2)
    slots = rm.affordable_slots(budget, len(holdings2))  # 계좌 금액에 자동 적응
    log(f"    매수예산 {budget:,}원 · 편입가능 {slots}종목(예산 적응) · 순자산 {net2:,}원")
    if slots <= 0 or budget < rm.r["min_order_amount"]:
        log("    예산 부족 — 매수 없음")
        return

    # AI 종목 선정은 결정 주기(config.AI_DECISION_INTERVAL_MIN)마다 1회만. 그 외엔 저장 계획 재사용.
    if plan_store.is_due(config.AI_DECISION_INTERVAL_MIN):
        cands = scanner.scan_candidates(days=2)
        picks = analyzer.select_portfolio(cands, cash=budget, holdings=holdings2, max_slots=slots,
                                          aggressive=(config.KIS_ENV == "vts"))
        plan_store.write(picks)
        log("    🧠 AI 새 결정")
    else:
        picks = plan_store.read().get("picks", [])
        log(f"    (AI 결정 {plan_store.age_min()}분 전 계획 재사용 · 다음 결정까지 대기)")

    picks = [p for p in picks if p["code"] not in held]  # 이미 보유·중복 편입 방지
    if not picks:
        log("    신규 매수 없음 — 관망 (보유 중이거나 계획 소진)")
        return

    each = rm.size_each(budget, len(picks), net2)
    for p in picks:
        if each < rm.r["min_order_amount"]:
            continue
        try:
            price = kis_client.get_current_price(p["code"])["price"]
        except Exception:
            continue
        if price <= 0:
            continue
        if price < rm.r["min_share_price"]:  # 동전주·저가 부실주 차단
            log(f"    · {p['name']}({p['code']}) {price:,}원 < 최저{rm.r['min_share_price']:,} — 건너뜀")
            continue
        qty = each // price
        if qty < 1:  # 주가가 종목당 예산보다 비싸 1주도 못 삼 → 건너뜀
            log(f"    · {p['name']}({p['code']}) {price:,}원 > 종목당예산 {each:,} — 건너뜀")
            continue
        res = kis_client.place_order(p["code"], "BUY", qty, market=True)
        if res["ok"]:
            _record_position(p["code"], p.get("horizon", ""), p.get("holding_period", ""))
        _log_trade({
            "side": "BUY", "code": p["code"], "name": p["name"],
            "qty": qty, "price": price, "amount": qty * price,
            "reason": p["reason"], "horizon": p.get("horizon", ""),
            "period": p.get("holding_period", ""),
            "ok": res["ok"], "msg": res["msg"], "order_no": res["order_no"],
        })
        mark = "✅" if res["ok"] else "❌"
        log(f"    {mark} 매수 {p['name']}({p['code']}) {qty}주 × {price:,} · 확신 {p['conviction']} · {res['msg']}")


def main() -> None:
    config.require("KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY", "KIS_ACCOUNT_NO")
    loop = "--loop" in sys.argv
    if not loop:
        try:
            run_cycle()
        except Exception as e:
            log("❌ [사이클 오류]", e)
        return
    log(f"자율 매매 루프 시작 ({LOOP_INTERVAL}초 간격, 정규장에만 매매). Ctrl+C 종료.")
    while True:
        try:
            run_cycle()
        except SystemExit:
            raise
        except Exception as e:
            log("❌ [사이클 오류]", e)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
