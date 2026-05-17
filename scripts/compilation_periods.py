# 시간대별/일별 모음 팟캐스트의 시간대 정의 (스크립트와 라우트 공유)

# (key, label_kr, start_hour_inclusive, end_hour_inclusive)
PERIODS = [
    ("dawn",      "새벽", 0,  5),
    ("morning",   "아침", 6,  11),
    ("afternoon", "낮",   12, 17),
    ("evening",   "저녁", 18, 21),
    ("night",     "밤",   22, 23),
]

PERIOD_KEYS = [p[0] for p in PERIODS]
PERIOD_BY_KEY = {p[0]: p for p in PERIODS}


def hour_to_period(hour: int) -> str:
    """0~23 시각을 시간대 key로 변환."""
    for key, _label, start, end in PERIODS:
        if start <= hour <= end:
            return key
    raise ValueError(f"invalid hour: {hour}")


def period_label(key: str) -> str:
    return PERIOD_BY_KEY[key][1]


def period_range(key: str):
    """(start_hour, end_hour_inclusive) 반환."""
    p = PERIOD_BY_KEY[key]
    return p[2], p[3]
