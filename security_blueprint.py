"""sosig.shop/security — 보안 대시보드.

로그·fail2ban·OpenVAS 결과를 한 화면에 요약.
Gabia flat 모듈 배치: /opt/flask-app/security_blueprint.py

데이터 소스:
- /var/log/nginx/access.log (최근 24h 실시간 파싱)
- /var/log/auth.log (SSH 시도, 최근 24h)
- fail2ban-client status sshd (sudo NOPASSWD)
- MySQL cve_monitor.openvas_findings / openvas_reports

권한 요구:
- flask 유저가 adm 그룹 (로그 읽기)
- sudoers: /usr/bin/fail2ban-client status [sshd]
"""
import ipaddress
import os
import re
import socket
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pymysql
from database import get_cve_db
from flask import Blueprint, render_template, jsonify

security_bp = Blueprint("security", __name__, url_prefix="/security")

# ============================================================================
# 데이터 소스
# ============================================================================

NGINX_ACCESS_LOG = "/var/log/nginx/access.log"
AUTH_LOG = "/var/log/auth.log"
GEOIP_CACHE_DB = "/opt/flask-app/cache/geoip.db"

# 감시 대상 민감 경로 (스캐너 감지)
SENSITIVE_PATTERNS = [
    r"/wp-",
    r"/wordpress",
    r"/admin",
    r"/phpmyadmin",
    r"/\.env",
    r"/\.git",
    r"/\.aws",
    r"/config\.",
    r"/xmlrpc\.php",
    r"/cgi-bin",
    r"/manager/html",
    r"/actuator",
    r"/openid-connect",
    r"/api/v\d+/users",
]
_SENSITIVE_RE = re.compile("|".join(SENSITIVE_PATTERNS), re.IGNORECASE)


# ============================================================================
# GeoIP (tcp_traceroute 캐시 재사용)
# ============================================================================

def _geoip_lookup(ip: str) -> Optional[str]:
    """국가명만 캐시에서 조회 (외부 API 호출 없음 — 대시보드 속도 우선)."""
    try:
        conn = sqlite3.connect(f"file:{GEOIP_CACHE_DB}?mode=ro", uri=True, timeout=1)
        row = conn.execute("SELECT country FROM geoip WHERE ip=?", (ip,)).fetchone()
        conn.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _is_private_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


# ============================================================================
# nginx access.log 파싱
# ============================================================================

# nginx combined format:
# IP - - [22/Apr/2026:14:55:03 +0900] "GET /path HTTP/1.1" 200 1234 "referer" "UA"
_NGINX_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) \S+" '
    r'(?P<status>\d+) (?P<size>\d+)'
)
_NGINX_TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"


def _parse_nginx_recent(hours: int = 24) -> List[Dict]:
    """access.log에서 최근 N시간 내 이벤트."""
    if not os.path.exists(NGINX_ACCESS_LOG):
        return []
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)
    events = []
    try:
        with open(NGINX_ACCESS_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _NGINX_RE.match(line)
                if not m:
                    continue
                try:
                    t = datetime.strptime(m.group("time"), _NGINX_TIME_FMT)
                except ValueError:
                    continue
                if t < cutoff:
                    continue
                events.append({
                    "ip": m.group("ip"),
                    "time": t,
                    "method": m.group("method"),
                    "path": m.group("path"),
                    "status": int(m.group("status")),
                })
    except (OSError, PermissionError):
        return []
    return events


def _summarize_nginx(events: List[Dict]) -> Dict:
    """access 로그 이벤트 집계."""
    total = len(events)
    by_status = Counter()
    by_country = Counter()
    by_ip = Counter()
    sensitive_hits = []
    errors_4xx = 0
    errors_5xx = 0

    for ev in events:
        by_status[ev["status"]] += 1
        by_ip[ev["ip"]] += 1
        if _SENSITIVE_RE.search(ev["path"]):
            sensitive_hits.append({
                "time": ev["time"].strftime("%m-%d %H:%M"),
                "ip": ev["ip"],
                "path": ev["path"][:80],
                "status": ev["status"],
                "country": _geoip_lookup(ev["ip"]) or "",
            })
        if 400 <= ev["status"] < 500:
            errors_4xx += 1
        elif 500 <= ev["status"] < 600:
            errors_5xx += 1

    # 국가별 집계 (GeoIP 캐시 있을 때만)
    for ip, cnt in by_ip.most_common(100):
        country = _geoip_lookup(ip)
        if country:
            by_country[country] += cnt

    # Top 20 IP
    top_ips = []
    for ip, cnt in by_ip.most_common(20):
        top_ips.append({
            "ip": ip,
            "count": cnt,
            "country": _geoip_lookup(ip) or "",
            "is_private": _is_private_ip(ip),
        })

    return {
        "total": total,
        "errors_4xx": errors_4xx,
        "errors_5xx": errors_5xx,
        "unique_ips": len(by_ip),
        "by_country": dict(by_country.most_common(10)),
        "top_ips": top_ips,
        "sensitive_hits": sensitive_hits[:30],
        "sensitive_count": len(sensitive_hits),
    }


# ============================================================================
# auth.log (SSH) 파싱
# ============================================================================

# "Apr 22 14:15:03 sosig sshd[1234]: Failed password for root from 1.2.3.4 port 1234"
# "Apr 22 14:15:10 sosig sshd[1234]: Accepted publickey for root from 1.2.3.4 port 1234"
_SSH_FAIL_RE = re.compile(r"sshd\[\d+\]: Failed password for (\S+) from (\S+)")
_SSH_INVALID_RE = re.compile(r"sshd\[\d+\]: Invalid user (\S+) from (\S+)")
_SSH_ACCEPT_RE = re.compile(r"sshd\[\d+\]: Accepted \S+ for (\S+) from (\S+)")
# Ubuntu 24.04 rsyslog: RFC3339 "2026-04-22T14:15:03.123456+09:00"
# 구형: "Apr 22 14:15:03"
_RFC3339_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
_SYSLOG_OLD_TIME_RE = re.compile(r"^(\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2})")


def _parse_syslog_time(line: str) -> Optional[datetime]:
    # 우선 RFC3339 시도 (Ubuntu 24.04 기본)
    if m := _RFC3339_TIME_RE.match(line):
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            return None
    # 폴백: 기존 syslog 형식 (로컬 tz 부여)
    if m := _SYSLOG_OLD_TIME_RE.match(line):
        try:
            t = datetime.strptime(m.group(1), "%b %d %H:%M:%S")
            now = datetime.now().astimezone()
            t = t.replace(year=now.year, tzinfo=now.tzinfo)
            if t > now + timedelta(days=1):
                t = t.replace(year=now.year - 1)
            return t
        except ValueError:
            return None
    return None


def _parse_ssh_recent(hours: int = 24) -> Dict:
    """auth.log에서 최근 N시간 SSH 이벤트."""
    if not os.path.exists(AUTH_LOG):
        return {"failed": [], "invalid": [], "accepted": [], "total_fail": 0,
                "total_accept": 0, "unique_fail_ips": 0, "top_attackers": [],
                "recent_accepted": []}
    cutoff = datetime.now().astimezone() - timedelta(hours=hours)
    failed = []
    invalid = []
    accepted = []
    fail_ip_counter = Counter()

    try:
        with open(AUTH_LOG, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                t = _parse_syslog_time(line)
                if not t:
                    continue
                if t.tzinfo is None:
                    t = t.astimezone()
                if t < cutoff:
                    continue
                if m := _SSH_FAIL_RE.search(line):
                    failed.append({"time": t, "user": m.group(1), "ip": m.group(2)})
                    fail_ip_counter[m.group(2)] += 1
                elif m := _SSH_INVALID_RE.search(line):
                    invalid.append({"time": t, "user": m.group(1), "ip": m.group(2)})
                    fail_ip_counter[m.group(2)] += 1
                elif m := _SSH_ACCEPT_RE.search(line):
                    accepted.append({"time": t, "user": m.group(1), "ip": m.group(2)})
    except (OSError, PermissionError):
        pass

    # Top 공격 IP
    top_attackers = []
    for ip, cnt in fail_ip_counter.most_common(15):
        top_attackers.append({
            "ip": ip,
            "count": cnt,
            "country": _geoip_lookup(ip) or "",
            "is_private": _is_private_ip(ip),
        })

    return {
        "total_fail": len(failed) + len(invalid),
        "total_accept": len(accepted),
        "unique_fail_ips": len(fail_ip_counter),
        "top_attackers": top_attackers,
        "recent_accepted": [
            {"time": a["time"].strftime("%m-%d %H:%M"),
             "ip": a["ip"],
             "country": _geoip_lookup(a["ip"]) or ""}
            for a in accepted[-10:][::-1]
        ],
    }


# ============================================================================
# fail2ban
# ============================================================================

def _fail2ban_status() -> Dict:
    try:
        out = subprocess.run(
            ["sudo", "-n", "/usr/bin/fail2ban-client", "status", "sshd"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"banned": [], "total_banned": 0, "total_failed": 0, "currently_banned": 0}

    stats = {"banned": [], "total_banned": 0, "total_failed": 0, "currently_banned": 0}
    for line in out.splitlines():
        line = line.strip().strip("|`-")
        if "Currently banned:" in line:
            stats["currently_banned"] = int(line.split(":")[-1].strip())
        elif "Total banned:" in line:
            stats["total_banned"] = int(line.split(":")[-1].strip())
        elif "Total failed:" in line:
            stats["total_failed"] = int(line.split(":")[-1].strip())
        elif "Banned IP list:" in line:
            ips = line.split(":", 1)[1].split()
            for ip in ips:
                stats["banned"].append({
                    "ip": ip,
                    "country": _geoip_lookup(ip) or "",
                })
    return stats


# ============================================================================
# OpenVAS 요약 (DB 쿼리)
# ============================================================================

def _db_conn():
    return get_cve_db()


def _openvas_summary() -> Dict:
    try:
        conn = _db_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                SUM(severity >= 9)                   AS critical,
                SUM(severity >= 7 AND severity < 9)  AS high,
                SUM(severity >= 4 AND severity < 7)  AS medium,
                SUM(severity > 0 AND severity < 4)   AS low,
                COUNT(*)                             AS total,
                COUNT(DISTINCT host_ip)              AS hosts
            FROM openvas_findings
        """)
        row = cur.fetchone() or {}
        cur.execute("SELECT MAX(scan_ended) AS t FROM openvas_reports")
        last = cur.fetchone()
        last_scan = last["t"].strftime("%Y-%m-%d %H:%M") if last and last["t"] else None
        conn.close()
        return {
            "critical": int(row.get("critical") or 0),
            "high": int(row.get("high") or 0),
            "medium": int(row.get("medium") or 0),
            "low": int(row.get("low") or 0),
            "total": int(row.get("total") or 0),
            "hosts": int(row.get("hosts") or 0),
            "last_scan": last_scan,
        }
    except Exception:
        return {"critical": 0, "high": 0, "medium": 0, "low": 0,
                "total": 0, "hosts": 0, "last_scan": None}


# ============================================================================
# 라우트
# ============================================================================

@security_bp.route("/", strict_slashes=False)
def dashboard():
    nginx_events = _parse_nginx_recent(24)
    web = _summarize_nginx(nginx_events)
    ssh = _parse_ssh_recent(24)
    f2b = _fail2ban_status()
    ov = _openvas_summary()

    return render_template(
        "security.html",
        web=web, ssh=ssh, f2b=f2b, openvas=ov,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


@security_bp.route("/api/summary")
def api_summary():
    nginx_events = _parse_nginx_recent(24)
    return jsonify({
        "web": _summarize_nginx(nginx_events),
        "ssh": _parse_ssh_recent(24),
        "fail2ban": _fail2ban_status(),
        "openvas": _openvas_summary(),
    })
