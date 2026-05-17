"""sosig.shop/subway — 지하철 실시간 위치 + 역별 도착 정보."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from flask import Blueprint, render_template, Response, abort, jsonify, request

subway_bp = Blueprint("subway", __name__, url_prefix="/subway")

DATA_DIR = Path("/opt/flask-app/data")

LINES = {
    "sinbundang": {
        "label": "신분당선",
        "api_name": "신분당선",
        "subway_id": 1077,
        "color": "#d31145",       # 하행 (광교) — 노선 본선 색
        "color_alt": "#3b82f6",   # 상행 (신사) — 명확히 다른 파란 계열
        "emoji": "🚇",
        "stations": [
            "신사", "논현", "신논현", "강남", "양재", "양재시민의숲",
            "청계산입구", "판교", "정자", "미금", "동천", "수지구청",
            "성복", "상현", "광교중앙", "광교",
        ],
        "major": ["신사", "강남", "양재", "판교", "정자", "광교중앙", "광교"],
        "active": True,
    },
    "line1": {"label": "1호선", "api_name": "1호선", "subway_id": 1001,
              "color": "#0052a4", "color_alt": "#3d75c0", "emoji": "1️⃣",
              "stations": [], "major": [], "active": False},
    "line2": {"label": "2호선", "api_name": "2호선", "subway_id": 1002,
              "color": "#00a84d", "color_alt": "#5cc78b", "emoji": "2️⃣",
              "stations": [], "major": [], "active": False},
    "line9": {"label": "9호선", "api_name": "9호선", "subway_id": 1009,
              "color": "#bb8336", "color_alt": "#d3a861", "emoji": "9️⃣",
              "stations": [], "major": [], "active": False},
}


# ── 도착 정보 캐시 (30초 TTL — 추가 API 호출 최소화) ─────────────────
ARRIVAL_TTL = 30
_arrival_cache = {}        # station_name -> (timestamp, data)
_arrival_lock = threading.Lock()


def _data_file(slug: str) -> Path:
    return DATA_DIR / f"subway_{slug}.json"


def _load_cache(slug: str) -> dict:
    f = _data_file(slug)
    if not f.exists():
        return {"trains": [], "fetched_at": 0}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"trains": [], "fetched_at": 0}


def _active_lines_data() -> list:
    out = []
    for slug, conf in LINES.items():
        if not conf["active"]:
            continue
        d = _load_cache(slug)
        out.append({
            "slug": slug, "label": conf["label"],
            "color": conf["color"], "color_alt": conf["color_alt"],
            "emoji": conf["emoji"], "stations": conf["stations"],
            "major": conf["major"], "trains": d.get("trains", []),
            "fetched_at": d.get("fetched_at", 0),
            "operating": d.get("operating", True),    # poller가 운행 종료 시 false 기록
            "subway_id": conf["subway_id"],
        })
    return out


# ── 페이지 ────────────────────────────────────────────────────────────
@subway_bp.route("/", strict_slashes=False)
def index():
    return render_template("subway_map.html",
                           lines=_active_lines_data(),
                           all_lines=[{"slug": s, **c} for s, c in LINES.items()])


@subway_bp.route("/stream")
def unified_stream():
    """SSE — 5분 maxage 후 자동 종료. 클라이언트는 자동 재연결."""
    def gen():
        d = _active_lines_data()
        yield f"data: {json.dumps(d, ensure_ascii=False)}\n\n"
        # 5분 동안 5초 간격으로 cached data push (재시작 시 빠른 종료를 위해 짧은 sleep)
        for _ in range(60):                   # 60 × 5s = 5분
            for _ in range(5):                # 5회 × 1초 = 5초 (1초마다 break 가능성)
                time.sleep(1)
            d = _active_lines_data()
            yield f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })


@subway_bp.route("/<slug>")
def line_redirect(slug: str):
    if slug not in LINES:
        abort(404)
    from flask import redirect
    return redirect(f"/subway#{slug}", code=302)


@subway_bp.route("/<slug>.json")
def line_json(slug: str):
    if slug not in LINES:
        abort(404)
    d = _load_cache(slug)
    d["stations"] = LINES[slug]["stations"]
    d["label"] = LINES[slug]["label"]
    d["active"] = LINES[slug]["active"]
    return d


# ── 역별 실시간 도착 정보 ─────────────────────────────────────────────
@subway_bp.route("/<slug>/<station>/arrivals")
def station_arrivals(slug: str, station: str):
    if slug not in LINES or not LINES[slug]["active"]:
        return jsonify({"error": "inactive line"}), 404
    conf = LINES[slug]
    if station not in conf["stations"]:
        return jsonify({"error": "unknown station"}), 404

    cache_key = f"{slug}:{station}"
    now = time.time()
    with _arrival_lock:
        cached = _arrival_cache.get(cache_key)
        if cached and (now - cached[0]) < ARRIVAL_TTL:
            return jsonify(cached[1])

    # API 호출 — 도착 정보 전용 키 (없으면 폴링 키로 폴백)
    api_key = os.environ.get("SEOUL_API_KEY_ARRIVAL") or os.environ.get("SEOUL_API_KEY", "")
    if not api_key:
        return jsonify({"error": "no api key"}), 500
    enc = urllib.parse.quote(station)
    url = (
        f"http://swopenAPI.seoul.go.kr/api/subway/{api_key}"
        f"/json/realtimeStationArrival/0/20/{enc}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("errorMessage", {}).get("code") != "INFO-000":
            err = data.get("errorMessage", {}).get("message", "")
            # 도착 정보 없을 때 (해당 역 없거나 운행 시간 외)
            if "없습니다" in err or "EXIST" in str(data):
                result = {"arrivals": [], "station": station, "fetched_at": int(now), "msg": err}
                with _arrival_lock:
                    _arrival_cache[cache_key] = (now, result)
                return jsonify(result)
            return jsonify({"error": err}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    arrivals_raw = data.get("realtimeArrivalList", [])
    sub_id_str = str(conf["subway_id"])
    filtered = []
    for a in arrivals_raw:
        if str(a.get("subwayId", "")) != sub_id_str:
            continue
        try:
            barvl = int(a.get("barvlDt") or 999999)
        except (ValueError, TypeError):
            barvl = 999999
        filtered.append({
            "subwayId": a.get("subwayId"),
            "updnLine": a.get("updnLine"),
            "trainLineNm": a.get("trainLineNm"),
            "bstatnNm": a.get("bstatnNm"),
            "barvlDt": barvl,
            "btrainNo": a.get("btrainNo"),
            "arvlMsg2": a.get("arvlMsg2"),
            "arvlMsg3": a.get("arvlMsg3"),
            "arvlCd": a.get("arvlCd"),
            "recptnDt": a.get("recptnDt"),
        })
    filtered.sort(key=lambda a: a["barvlDt"])

    result = {"arrivals": filtered, "station": station, "fetched_at": int(now)}
    with _arrival_lock:
        _arrival_cache[cache_key] = (now, result)
    return jsonify(result)
