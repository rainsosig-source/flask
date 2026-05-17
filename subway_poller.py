#!/usr/bin/env python3
"""신분당선 실시간 위치 폴링 — systemd 단일 프로세스.

결과를 /opt/flask-app/data/subway_sinbundang.json 에 저장.
Flask는 그 파일을 읽기만 함 (worker 수와 무관하게 일일 호출 수 1회 단위 제어).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

OUT_PATH = Path("/opt/flask-app/data/subway_sinbundang.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_api_key() -> str:
    # systemd EnvironmentFile=/opt/flask-app/.env 가 이미 환경변수에 SEOUL_API_KEY 로드
    key = os.environ.get("SEOUL_API_KEY", "")
    if key:
        return key
    # 폴백: .env 직접 읽기
    env = Path("/opt/flask-app/.env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("SEOUL_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def is_operating() -> bool:
    """신분당선 운영 시간 (대략 05:30 ~ 익일 00:30)."""
    now = datetime.now()
    h, m = now.hour, now.minute
    # 5:30 ~ 23:59
    if 5 <= h <= 23:
        if h == 5 and m < 30:
            return False
        return True
    # 0:00 ~ 0:30 (전날 막차의 잔여 운행)
    if h == 0 and m < 30:
        return True
    return False


def current_interval() -> int:
    """피크/비피크 차등 폴링. 운행 시간 외엔 큰 값 반환(루프에서 운행 시작 대기)."""
    if not is_operating():
        return 1800       # 30분마다 운행 시작 여부만 확인
    h = datetime.now().hour
    if 7 <= h < 10 or 17 <= h < 20:
        return 60         # 피크
    return 90             # 일반


def fetch_once(key: str):
    line_name = urllib.parse.quote("신분당선")
    url = (
        f"http://swopenAPI.seoul.go.kr/api/subway/{key}"
        f"/json/realtimePosition/0/100/{line_name}"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"[fetch] 오류: {e}", file=sys.stderr)
        return {}


def normalize(api_resp: dict) -> list:
    out = []
    for t in api_resp.get("realtimePositionList", []):
        out.append({
            "trainNo": t.get("trainNo", ""),
            "statn": t.get("statnNm", ""),
            "dest": t.get("statnTnm", ""),
            "updnLine": int(t.get("updnLine", 0)),
            "trainSttus": int(t.get("trainSttus", 0)),
            "directAt": t.get("directAt", ""),
            "lstcarAt": t.get("lstcarAt", ""),
        })
    return out


def write_atomic(data: dict):
    tmp = OUT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(OUT_PATH)


def main():
    key = _load_api_key()
    if not key:
        print("SEOUL_API_KEY 없음", file=sys.stderr)
        sys.exit(1)

    daily_count_file = Path("/opt/flask-app/data/subway_count.json")
    while True:
        # 일일 카운트 (UTC가 아니라 KST 기준)
        today = datetime.now().strftime("%Y-%m-%d")
        cnt = {}
        if daily_count_file.exists():
            try:
                cnt = json.loads(daily_count_file.read_text())
            except Exception:
                cnt = {}
        if cnt.get("date") != today:
            cnt = {"date": today, "count": 0}

        # 안전 가드: 950건 이상이면 폴링 중단 (10분 후 재확인)
        if cnt["count"] >= 950:
            print(f"[guard] 일일 한도 임박 ({cnt['count']}/1000) — 10분 휴식", file=sys.stderr)
            time.sleep(600)
            continue

        # 운행 시간 외 — 폴링 안 함, 운행 종료 마커만 파일에 기록
        if not is_operating():
            write_atomic({
                "trains": [],
                "fetched_at": int(time.time()),
                "operating": False,
                "today_count": cnt["count"],
            })
            print(f"[idle] 운행 시간 외 — 30분 대기")
            time.sleep(1800)
            continue

        interval = current_interval()
        try:
            data = fetch_once(key)
            if data and data.get("errorMessage", {}).get("code") == "INFO-000":
                trains = normalize(data)
                write_atomic({
                    "trains": trains,
                    "fetched_at": int(time.time()),
                    "operating": True,
                    "today_count": cnt["count"] + 1,
                })
                cnt["count"] += 1
                daily_count_file.write_text(json.dumps(cnt))
                print(f"[ok] {len(trains)} trains, daily={cnt['count']}/1000")
            else:
                err = data.get("errorMessage", {}) if data else {}
                print(f"[err] {err}", file=sys.stderr)
        except Exception as e:
            print(f"[loop] 예외: {e}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    main()
