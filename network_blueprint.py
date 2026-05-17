"""네트워크 트래픽 일별 분석 (/network) — owner 전용, 텔레그램 OTP 인증."""
import os
import json
import random
import secrets
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, jsonify, request, session, redirect, current_app
from database import get_cve_db

network_bp = Blueprint('network', __name__)

KST = timezone(timedelta(hours=9))

# OTP 저장 (in-memory, single-worker 가정)
# key = session id, value = {"code": "123456", "expires": epoch, "attempts": 0}
_OTP_STORE: dict[str, dict] = {}
OTP_TTL_SEC = 300            # OTP 유효 5분
SESSION_TTL_SEC = 3600       # 인증 세션 1시간
MAX_OTP_ATTEMPTS = 3
RATE_LIMIT_SEC = 30          # OTP 재요청 최소 간격

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")


def _now() -> float:
    return time.time()


def _gc_otp():
    """만료된 OTP 항목 정리."""
    now = _now()
    expired = [k for k, v in _OTP_STORE.items() if v["expires"] < now]
    for k in expired:
        _OTP_STORE.pop(k, None)


def _is_authed() -> bool:
    expiry = session.get("network_authed_until", 0)
    return _now() < expiry


def _send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        current_app.logger.error("Telegram BOT_TOKEN/ADMIN_CHAT_ID 미설정")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        current_app.logger.error(f"Telegram send 실패: {e}")
        return False


@network_bp.route("/api/auth/network/request_otp", methods=["POST"])
def request_otp():
    _gc_otp()
    sid = session.get("network_sid")
    if not sid:
        sid = secrets.token_hex(16)
        session["network_sid"] = sid

    # rate limit: 30초 내 재요청 차단
    existing = _OTP_STORE.get(sid)
    if existing:
        elapsed = OTP_TTL_SEC - (existing["expires"] - _now())
        if elapsed < RATE_LIMIT_SEC and existing["attempts"] == 0:
            return jsonify({"ok": False, "error": f"{int(RATE_LIMIT_SEC - elapsed)}초 후 재요청"}), 429

    code = f"{random.randint(0, 999999):06d}"
    _OTP_STORE[sid] = {"code": code, "expires": _now() + OTP_TTL_SEC, "attempts": 0}

    msg = (
        f"🔐 *sosig.shop 네트워크 분석 접속 인증*\n\n"
        f"코드: `{code}`\n\n"
        f"5분 내 입력하세요. 본인이 요청한 게 아니면 무시."
    )
    sent = _send_telegram(msg)
    if not sent:
        return jsonify({"ok": False, "error": "텔레그램 전송 실패"}), 500
    return jsonify({"ok": True, "ttl_sec": OTP_TTL_SEC})


@network_bp.route("/api/auth/network/verify_otp", methods=["POST"])
def verify_otp():
    _gc_otp()
    sid = session.get("network_sid")
    code_in = (request.json or {}).get("code", "").strip()
    if not sid or not code_in:
        return jsonify({"ok": False, "error": "코드 없음"}), 400

    rec = _OTP_STORE.get(sid)
    if not rec:
        return jsonify({"ok": False, "error": "OTP 만료 — 재요청"}), 410

    rec["attempts"] += 1
    if rec["attempts"] > MAX_OTP_ATTEMPTS:
        _OTP_STORE.pop(sid, None)
        return jsonify({"ok": False, "error": "시도 횟수 초과 — 재요청"}), 429
    if rec["code"] != code_in:
        return jsonify({"ok": False, "error": f"불일치 ({MAX_OTP_ATTEMPTS - rec['attempts']}회 남음)"}), 401

    _OTP_STORE.pop(sid, None)
    session["network_authed_until"] = _now() + SESSION_TTL_SEC
    return jsonify({"ok": True, "ttl_sec": SESSION_TTL_SEC})


@network_bp.route("/api/auth/network/logout", methods=["POST"])
def logout():
    session.pop("network_authed_until", None)
    return jsonify({"ok": True})


@network_bp.route("/network/demo", strict_slashes=False)
def network_demo_page():
    return render_template("network.html", demo=True)


@network_bp.route("/api/network/demo/reports.json")
def demo_reports_json():
    from network_demo_data import DEMO_DATA
    r = dict(DEMO_DATA)
    return jsonify({
        "reports": [{
            "id": 0,
            "report_date": r["report_date"],
            "created_at": r["created_at"],
            "stats_json": {
                "conn_total": r["stats_json"]["conn_total"],
                "real_alerts": r["stats_json"]["real_alerts"],
            },
        }],
        "_now": "DEMO",
    })


@network_bp.route("/api/network/demo/reports/<date>")
def demo_report_detail(date):
    from network_demo_data import DEMO_DATA
    return jsonify(DEMO_DATA)


@network_bp.route("/network", strict_slashes=False)
def network_page():
    if not _is_authed():
        return redirect("/")
    return render_template("network.html")


@network_bp.route("/api/network/reports.json")
def reports_json():
    if not _is_authed():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    conn = get_cve_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, report_date, stats_json, created_at,
                       CHAR_LENGTH(summary_md) AS summary_len
                FROM network_reports
                ORDER BY report_date DESC LIMIT 60
            """)
            rows = cur.fetchall()
            for r in rows:
                if r.get("report_date"):
                    r["report_date"] = r["report_date"].strftime("%Y-%m-%d")
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M")
                v = r.get("stats_json")
                if isinstance(v, str):
                    try:
                        r["stats_json"] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        r["stats_json"] = {}
    finally:
        conn.close()
    return jsonify({"reports": rows, "_now": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")})


@network_bp.route("/api/network/reports/<date>")
def report_detail(date: str):
    if not _is_authed():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    conn = get_cve_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, report_date, summary_md, llm_raw_md, stats_json, created_at
                FROM network_reports WHERE report_date=%s
            """, (date,))
            r = cur.fetchone()
            if not r:
                return jsonify({"error": "not found"}), 404
            if r.get("report_date"):
                r["report_date"] = r["report_date"].strftime("%Y-%m-%d")
            if r.get("created_at"):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M")
            v = r.get("stats_json")
            if isinstance(v, str):
                try:
                    r["stats_json"] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    r["stats_json"] = {}
    finally:
        conn.close()
    return jsonify(r)
