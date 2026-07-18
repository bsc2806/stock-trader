"""자율 매매 드라이런 (주문 실행 없음).

전체 흐름을 한 번에 확인:
  잔고 → 보유종목 손절/익절 판정 → 시장 스캔(발굴) → AI 종목 선정 → 리스크 사이징 → 매매 '계획' 출력

실제 주문은 하지 않는다. AI의 자율 판단이 합리적인지 눈으로 검증하는 단계.
"""
import config
import kis_client
import scanner
import risk
import analyzer


def main() -> None:
    config.require("KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY", "KIS_ACCOUNT_NO")
    rm = risk.RiskManager()

    # 1) 계좌 상태
    bal = kis_client.get_balance()
    net = bal["summary"]["net_asset"] or bal["summary"]["total_eval"]
    cash = bal["summary"]["cash"]
    holdings = bal["holdings"]
    equity = max(0, net - cash)  # 주식 평가액 근사

    print("=== 자율 매매 드라이런 (주문 없음) ===")
    print(f"순자산 {net:,}원 · 예수금 {cash:,}원 · 주식평가 {equity:,}원 · 보유 {len(holdings)}종목\n")

    # 2) 계좌 방어선 체크 (드라이런: 고점=순자산, 당일손익=0 가정)
    if rm.circuit_tripped(net, net):
        print("⛔ 서킷브레이커 발동 — 전량 청산 대상. 매수 중지.")
        return

    # 3) 보유 종목 손절/익절 판정
    if holdings:
        print("[보유 종목 판정]")
        for h in holdings:
            act = rm.position_action(h["pnl_rate"])
            tag = {"STOP": "손절", "TAKE": "익절", "HOLD": "유지"}[act]
            print(f"  {h['name']}({h['code']}) {h['pnl_rate']:+.2f}% → {tag}")
        print()

    # 4) 시장 스캔 (종목 발굴)
    cands = scanner.scan_candidates(days=2)
    print(f"[시장 스캔] 신호성 후보 {len(cands)}개 발굴")

    # 5) 리스크 예산
    slots = rm.slots_available(len(holdings))
    budget = rm.buy_budget(net, equity, cash)
    per_max = rm.max_per_position(net)
    print(f"[리스크] 신규 편입 가능 {slots}종목 · 매수예산 {budget:,}원 · 종목당 최대 {per_max:,}원\n")

    if slots <= 0 or budget < rm.r["min_order_amount"]:
        print("여유 슬롯/예산 부족 — 신규 매수 없음.")
        return

    # 6) AI 종목 선정 (여기서만 AI 호출 1회). 모의면 적극 모드.
    picks = analyzer.select_portfolio(cands, cash=budget, holdings=holdings, max_slots=slots,
                                      aggressive=(config.KIS_ENV == "vts"))
    if not picks:
        print("[AI 판단] 지금 확신 가는 매수 종목 없음 — 관망.")
        return

    # 7) 사이징 → 매수 계획 (장기/단기 구분)
    each = min(per_max, budget // len(picks))
    print(f"[AI 매수 계획] {len(picks)}종목 (종목당 약 {each:,}원)\n")
    for tag, hz in (("📈 장기 투자", "장기"), ("⚡ 단기 매매", "단기")):
        group = [p for p in picks if p.get("horizon") == hz]
        if not group:
            continue
        print(f"  {tag} ({len(group)}종목)")
        for p in group:
            print(f"    ▶ {p['name']}({p['code']}) · {each:,}원 · 확신 {p['conviction']} · 예상보유 {p.get('holding_period','?')}")
            print(f"       근거: {p['reason']}")
        print()

    print("\n=== 계획만 출력함. 실제 주문은 실행되지 않았습니다. ===")


if __name__ == "__main__":
    main()
