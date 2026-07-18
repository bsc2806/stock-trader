"""Phase 1 실행기 — 읽기 전용 (주문 없음).

흐름:
  1) KIS 연결 확인: 토큰 발급 + 감시종목 현재가 조회
  2) DART: 감시종목의 최근 공시 수집
  3) AI가 각 공시를 분석해 신호/근거를 '로그로만' 출력

실제 돈은 전혀 움직이지 않습니다. 파이프라인이 잘 도는지 눈으로 확인하는 단계.
"""
import json
from pathlib import Path

import config
import dart_client
import kis_client
import analyzer

ROOT = Path(__file__).resolve().parent.parent


def load_watchlist() -> list[dict]:
    data = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
    return data["stocks"]


def main() -> None:
    config.require("KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY")

    stocks = load_watchlist()
    print(f"=== Phase 1 (읽기 전용) | 감시종목 {len(stocks)}개 | KIS={config.KIS_ENV} ===\n")

    # 1) KIS 연결 확인 + 현재가
    print("[1] KIS 연결 확인 및 현재가")
    for s in stocks:
        try:
            q = kis_client.get_current_price(s["code"])
            print(f"    {s['name']}({s['code']}): {q['price']:,}원 ({q['change_rate']:+.2f}%)")
        except Exception as e:
            print(f"    {s['name']}({s['code']}): 조회 실패 - {e}")
    print()

    # 2) + 3) DART 공시 수집 → AI 분석
    print("[2] DART 공시 수집 + AI 분석")
    corp_map = dart_client.get_corp_map()

    for s in stocks:
        corp_code = corp_map.get(s["code"])
        if not corp_code:
            print(f"    {s['name']}: DART corp_code 매핑 없음 (건너뜀)")
            continue

        try:
            disclosures = dart_client.get_recent_disclosures(corp_code, days=3)
        except Exception as e:
            print(f"    {s['name']}: 공시 조회 실패 - {e}")
            continue

        if not disclosures:
            print(f"    {s['name']}: 최근 공시 없음")
            continue

        print(f"    {s['name']}: 공시 {len(disclosures)}건")
        for d in disclosures:
            title = f"       - {d.get('rcept_dt')} | {d.get('report_nm')}"
            try:
                result = analyzer.analyze_disclosure(s["name"], d)
                print(f"{title}\n         → [{result['signal']}] ({result['confidence']}) {result['reason']}")
            except Exception as e:
                print(f"{title}\n         → AI 분석 실패: {e}")
    print("\n=== 완료 (주문은 실행되지 않았습니다) ===")


if __name__ == "__main__":
    main()
