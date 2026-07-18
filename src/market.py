"""한국 증시 정규장 운영시간 판단 (서버·트레이더 공용).

평일 09:00~15:30. 공휴일은 아직 미반영(추후 휴장일 달력 추가 가능).
"""
import datetime as dt

_DOW_KR = ["월", "화", "수", "목", "금", "토", "일"]
OPEN_T = dt.time(9, 0)
CLOSE_T = dt.time(15, 30)


def status() -> dict:
    now = dt.datetime.now()
    is_weekend = now.weekday() >= 5
    t = now.time()
    is_open = (not is_weekend) and (OPEN_T <= t <= CLOSE_T)

    if is_open:
        session = "정규장"
    elif is_weekend:
        session = "주말 휴장"
    elif t < OPEN_T:
        session = "장 시작 전"
    else:
        session = "장 마감"

    if (not is_weekend) and t < OPEN_T:
        nxt = now
    else:
        nxt = now + dt.timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += dt.timedelta(days=1)
    nxt = nxt.replace(hour=9, minute=0, second=0, microsecond=0)

    return {
        "open": is_open,
        "session": session,
        "next_open": f"{_DOW_KR[nxt.weekday()]} {nxt.strftime('%m/%d %H:%M')}",
    }


def is_open() -> bool:
    return status()["open"]
