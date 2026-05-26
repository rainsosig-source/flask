# sosig.shop RAG 챗봇 프록시 — GB10(tailnet)의 sosig-chat 서비스로 전달.
import json, time, urllib.request
from pathlib import Path
from flask import Blueprint, render_template, request, jsonify

chat_bp = Blueprint("chat", __name__)
GB10_URL = "http://100.76.176.78:8900/api/chat"
try:
    TOKEN = Path("/opt/flask-app/.chat_token").read_text(encoding="utf-8").strip()
except Exception:
    TOKEN = ""

_HITS = {}
WINDOW, MAX_PER_HR, MIN_GAP = 3600, 30, 2


def _rate_limit(ip):
    now = time.time()
    h = [t for t in _HITS.get(ip, []) if now - t < WINDOW]
    if h and now - h[-1] < MIN_GAP:
        return "너무 빠릅니다. 잠시 후 다시 시도하세요."
    if len(h) >= MAX_PER_HR:
        return "시간당 질문 한도를 초과했습니다. 잠시 후 다시."
    h.append(now)
    _HITS[ip] = h
    return None


@chat_bp.route("/chat")
def chat_page():
    return render_template("chat.html")


@chat_bp.route("/api/chat", methods=["POST"])
def chat_api():
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr or "")).split(",")[0].strip()
    err = _rate_limit(ip)
    if err:
        return jsonify({"error": err}), 429
    q = ((request.get_json(silent=True) or {}).get("question") or "").strip()[:500]
    if not q:
        return jsonify({"error": "질문을 입력하세요."}), 400
    if not TOKEN:
        return jsonify({"error": "챗봇 미설정"}), 503
    try:
        req = urllib.request.Request(
            GB10_URL, data=json.dumps({"question": q}).encode("utf-8"),
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=200) as r:
            return jsonify(json.loads(r.read().decode("utf-8")))
    except Exception as e:
        return jsonify({"error": "챗봇 응답 실패 — 잠시 후 다시 시도하세요.", "detail": str(e)[:120]}), 502
