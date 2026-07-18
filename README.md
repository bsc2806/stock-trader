# AI 주식 투자 프로그램 (Phase 1 — 읽기 전용)

AI가 **뉴스/공시 정보를 분석**해 투자 신호를 내고, 검증 후 **모의투자 → 소액 실전** 순서로
진행하는 개인 프로젝트. 신뢰 소스(DART 공시 우선, 뉴스 보조)만 사용하고, '카더라'는 배제.

## 현재 단계: Phase 1
DART 공시 수집 → AI 분석 → **로그로만 출력**. 실제 주문은 없음 (안전).

- Phase 1: 읽기 전용 파이프라인 (지금)
- Phase 2: 모의투자 실제 주문 + 리스크 규칙
- Phase 3: 소액 실전

## 보안 원칙 🔒
- 모든 키는 `.env`에만 저장. `.env`는 `.gitignore`로 커밋/외부 유출 방지.
- **키를 채팅/코드에 붙여넣지 말고 `.env` 파일에 직접 입력.**
- AI는 신호만 생성. 실제 매매는 규칙(손절/한도)으로 감싼 뒤에만 (Phase 2).

## 설치
```bash
cd D:\workspace\ai-stock-trader
python -m venv .venv
.venv\Scripts\activate        # PowerShell
pip install -r requirements.txt
```

## 키 설정
1. `.env.example`을 복사해 `.env` 생성
2. 값 채우기:
   - `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` — 한국투자증권 **모의투자**용
   - `DART_API_KEY` — OpenDART 40자리 인증키
   - `KIS_ENV=vts` (모의투자 고정)

## 실행
```bash
python src/main.py
```
정상이면: 감시종목 현재가 → 최근 공시 → 각 공시에 대한 AI 신호가 출력됩니다.
(주문은 절대 실행되지 않습니다.)

## 감시종목 변경
`watchlist.json`의 `stocks` 배열 수정 (name=표시용, code=6자리 종목코드).

## 구조
```
src/
  config.py       # .env 로딩/검증
  dart_client.py  # DART 공시 수집 (종목코드→corp_code 매핑 포함)
  kis_client.py   # KIS 토큰/현재가 (읽기 전용, 주문 함수 없음)
  analyzer.py     # Claude 기반 공시 분석 (신호+근거)
  main.py         # Phase 1 실행기
watchlist.json    # 감시종목
.env              # 키 (직접 생성, 커밋 금지)
```

## TODO (다음 단계)
- [ ] 공시 전문(document) 파싱해 제목이 아닌 본문까지 분석
- [ ] 뉴스 소스(네이버 뉴스 API 등) 보조 컨텍스트 추가
- [ ] Phase 2: KIS 모의 주문 + 리스크 규칙(손절/익절/종목당 한도/일일 매매횟수)
- [ ] 판단/주문 로그 파일 저장 + 알림(텔레그램 등)
