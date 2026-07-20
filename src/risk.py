"""리스크 규칙 — 원금 보존이 최우선.

자율 매매라도 AI가 절대 넘을 수 없는 방어선을 코드로 강제한다.
값은 보수적 기본값이며 risk_config.json 으로 조정 가능.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 실전 기본 = 보수적 (원금 보존 최우선)
# 계좌 낙폭 방어는 '단계적': 매수중지 → 부분정리 → 전량청산 (한 번에 전량청산 X)
DEFAULT_RULES = {
    "max_equity_pct": 40.0,        # 순자산 중 주식 최대 비중 (나머지는 현금 방어)
    "max_position_pct": 12.0,      # 한 종목 최대 비중 (순자산 대비)
    "stop_loss_pct": -8.0,         # 기본 손절선 (horizon 미상 시)
    "take_profit_pct": 15.0,       # 기본 익절선 (horizon 미상 시)
    # 손실은 짧게(손절), 이익은 트레일링으로 길게. 발동 이후 고점 대비 giveback 만큼 꺾이면 익절.
    "short_stop_pct": -4.0,             # 단기 손절
    "short_trail_activate_pct": 5.0,    # 단기 트레일링 발동(이만큼 오르면 추적 시작)
    "short_trail_giveback_pct": 2.5,    # 단기 고점 대비 이만큼 반납하면 익절
    "short_hard_take_pct": 15.0,        # 단기 상한 익절(백업)
    "long_stop_pct": -12.0,             # 장기 손절
    "long_trail_activate_pct": 10.0,    # 장기 트레일링 발동
    "long_trail_giveback_pct": 5.0,     # 장기 반납 허용
    "long_hard_take_pct": 35.0,         # 장기 상한 익절
    "daily_loss_limit_pct": -3.0,  # 당일 손실 한도 → 당일 매매 중지
    "dd_halt_buy_pct": -10.0,      # 계좌 낙폭 이 이하 → 신규 매수 중지
    "dd_trim_pct": -15.0,          # 계좌 낙폭 이 이하 → 손실 종목 절반 정리
    "dd_liquidate_pct": -25.0,     # 계좌 낙폭 이 이하 → 전량 청산
    "max_positions": 6,            # 동시 보유 최대 종목 수(상한). 실제는 예산에 따라 자동 축소.
    "min_order_amount": 100000,    # 최소 주문 금액(원)
    "min_share_price": 2000,       # 이 미만 주가는 매수 금지(동전주·저가 부실주 차단)
}

# 모의 전용 = 적극적/실험적 (다양하게 시도해 학습). 낙폭 방어는 조금 더 여유.
MOCK_RULES = {
    "max_equity_pct": 80.0,        # 자본을 더 적극적으로 투입
    "max_position_pct": 20.0,
    "stop_loss_pct": -10.0,        # 기본 손절선 (horizon 미상 시)
    "take_profit_pct": 20.0,       # 기본 익절선 (horizon 미상 시)
    "short_stop_pct": -5.0,             # 단기 손절
    "short_trail_activate_pct": 6.0,    # 단기 트레일링 발동
    "short_trail_giveback_pct": 3.0,    # 단기 반납 허용
    "short_hard_take_pct": 20.0,        # 단기 상한 익절
    "long_stop_pct": -14.0,             # 장기 손절
    "long_trail_activate_pct": 12.0,    # 장기 트레일링 발동
    "long_trail_giveback_pct": 6.0,     # 장기 반납 허용
    "long_hard_take_pct": 40.0,         # 장기 상한 익절
    "daily_loss_limit_pct": -6.0,
    "dd_halt_buy_pct": -12.0,      # 신규 매수 중지
    "dd_trim_pct": -20.0,          # 손실 종목 절반 정리
    "dd_liquidate_pct": -30.0,     # 전량 청산 (진짜 붕괴에만)
    "max_positions": 6,            # 상한. 실제는 예산에 따라 자동 축소.
    "min_order_amount": 100000,
    "min_share_price": 500,        # 모의는 저가주 실험 허용(진짜 동전주만 차단)
}


def load_rules() -> dict:
    import config
    base = dict(MOCK_RULES if config.KIS_ENV == "vts" else DEFAULT_RULES)
    # risk_config.json 이 있으면 최종 오버라이드(사용자 조정)
    path = ROOT / "risk_config.json"
    if path.exists():
        try:
            base.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return base


class RiskManager:
    def __init__(self, rules: dict | None = None):
        self.r = rules or load_rules()

    # --- 계좌 전체 낙폭 방어 (단계적) ---
    def drawdown_pct(self, net_asset: int, peak_asset: int) -> float:
        """고점 대비 현재 낙폭(%). 음수일수록 손실."""
        if peak_asset <= 0:
            return 0.0
        return (net_asset - peak_asset) / peak_asset * 100

    def drawdown_level(self, net_asset: int, peak_asset: int) -> str:
        """낙폭 단계 판정: NORMAL / HALT_BUY / TRIM / LIQUIDATE."""
        dd = self.drawdown_pct(net_asset, peak_asset)
        if dd <= self.r["dd_liquidate_pct"]:
            return "LIQUIDATE"   # 전량 청산
        if dd <= self.r["dd_trim_pct"]:
            return "TRIM"        # 손실 종목 절반 정리 + 매수 중지
        if dd <= self.r["dd_halt_buy_pct"]:
            return "HALT_BUY"    # 신규 매수만 중지
        return "NORMAL"

    def daily_halt(self, day_pnl_pct: float) -> bool:
        """당일 손익률이 한도 이하면 당일 매매 중지."""
        return day_pnl_pct <= self.r["daily_loss_limit_pct"]

    # --- 개별 포지션: 손실은 짧게(손절), 이익은 트레일링으로 길게 ---
    def exit_decision(self, pnl_rate: float, peak_rate: float,
                      horizon: str | None = None) -> str:
        """청산 판정: STOP(손절) / TAKE(상한익절) / TRAIL(트레일링익절) / HOLD.

        peak_rate = 매수 후 도달한 최고 수익률(%). 트레일링은 고점 대비 반납폭으로 익절.
        """
        if horizon == "단기":
            stop = self.r["short_stop_pct"]
            act = self.r["short_trail_activate_pct"]
            give = self.r["short_trail_giveback_pct"]
            hard = self.r["short_hard_take_pct"]
        elif horizon == "장기":
            stop = self.r["long_stop_pct"]
            act = self.r["long_trail_activate_pct"]
            give = self.r["long_trail_giveback_pct"]
            hard = self.r["long_hard_take_pct"]
        else:
            # horizon 미상 → 고정 기준
            if pnl_rate <= self.r["stop_loss_pct"]:
                return "STOP"
            if pnl_rate >= self.r["take_profit_pct"]:
                return "TAKE"
            return "HOLD"

        if pnl_rate <= stop:
            return "STOP"                    # 손절 (손실 짧게)
        if pnl_rate >= hard:
            return "TAKE"                    # 상한 익절 (백업)
        if peak_rate >= act and pnl_rate <= peak_rate - give:
            return "TRAIL"                   # 트레일링 익절 (오른 이익 지키기)
        return "HOLD"                        # 계속 보유 (이익 길게)

    # --- 신규 매수 예산 ---
    def buy_budget(self, net_asset: int, current_equity: int, cash: int) -> int:
        """신규 매수에 쓸 수 있는 총액. (주식 비중 상한 + 보유현금 이내)"""
        max_equity = net_asset * self.r["max_equity_pct"] / 100
        room = max_equity - current_equity
        return int(max(0, min(room, cash)))

    def max_per_position(self, net_asset: int) -> int:
        """한 종목에 넣을 수 있는 최대 금액."""
        return int(net_asset * self.r["max_position_pct"] / 100)

    def slots_available(self, current_positions: int) -> int:
        """추가로 편입 가능한 종목 수 (상한)."""
        return max(0, self.r["max_positions"] - current_positions)

    def affordable_slots(self, budget: int, current_positions: int) -> int:
        """예산으로 감당 가능한 신규 편입 종목 수 = min(빈 슬롯, 예산/최소주문액).

        계좌 금액에 자동 적응: 50만원이면 1~2종목, 1천만원이면 최대 종목수까지.
        """
        by_budget = int(budget // self.r["min_order_amount"])
        return max(0, min(self.slots_available(current_positions), by_budget))

    def size_each(self, budget: int, num_picks: int, net_asset: int) -> int:
        """종목당 매수 금액. 소액이면 종목당%캡 대신 최소주문액까지 허용(그래야 체결됨)."""
        if num_picks <= 0:
            return 0
        cap = max(self.max_per_position(net_asset), self.r["min_order_amount"])
        return int(min(cap, budget // num_picks))


if __name__ == "__main__":
    rm = RiskManager()
    print("리스크 규칙:", rm.r)
    net = 10_000_000
    print("신규 매수 예산(현금 1천만, 보유 0):", f"{rm.buy_budget(net, 0, net):,}원")
    print("종목당 최대:", f"{rm.max_per_position(net):,}원")
    for dd in (-13, -22, -31):
        net = int(10_000_000 * (1 + dd / 100))
        print(f"낙폭 {dd}% → 단계:", rm.drawdown_level(net, 10_000_000))
    # 트레일링: 단기 고점 +10%에서 현재 +6% (반납 4% > 3%) → TRAIL 익절
    print("단기 고점10%→현재6%:", rm.exit_decision(6.0, 10.0, "단기"))
    print("단기 고점10%→현재9%:", rm.exit_decision(9.0, 10.0, "단기"), "(아직 보유)")
    print("단기 -5%(손절):", rm.exit_decision(-5.0, 3.0, "단기"))
    print("장기 고점12%→현재6%:", rm.exit_decision(6.0, 12.0, "장기"))
    print("당일 매매중지(-3.5%):", rm.daily_halt(-3.5))
