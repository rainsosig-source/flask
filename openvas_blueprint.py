"""sosig.shop/vuln 에 붙는 '내 인프라' 탭 블루프린트.

배치 위치: Gabia /opt/flask-app/openvas_blueprint.py
등록: app.register_blueprint(openvas_bp, url_prefix='/vuln')

노출 정책: 사설망/공인망 모두 실제 IP/호스트명은 공개하지 않는다.
- 192.168.x.x → 'scanner' / 'gateway' / 'lan-host-N' 라벨로만 표기
- 공개 IP → 'public-host-N' 으로 마스킹 (Cloudflare 뒤 오리진 IP 보호)

라우팅은 IP 대신 slug 로. 따라서 누가 직접 /vuln/infra/host/192.168.1.4 를
때려도 404.

경로:
- /vuln/infra              — 호스트별 심각도 대시보드
- /vuln/infra/host/<slug>  — 특정 호스트 finding 상세
- /vuln/infra/finding/<id> — finding 1건 AI writeup 포함 상세
- /vuln/infra/map          — D3 네트워크 지도
- /vuln/infra/api/map      — 맵용 JSON
- /vuln/infra/api/summary  — 집계 JSON
"""
import os
import pymysql
from database import get_cve_db
from flask import Blueprint, render_template, jsonify, abort

openvas_bp = Blueprint("openvas_infra", __name__)

SCANNER_IP = "192.168.1.4"

PRIVATE_PREFIXES = (
    "10.", "127.",
    "192.168.",
    "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
)


def _db():
    return get_cve_db()


def _is_private(ip: str) -> bool:
    return any(ip.startswith(p) for p in PRIVATE_PREFIXES)


def _build_ip_map(cur) -> dict:
    """openvas_hosts 전체에 대해 ip → {slug, label, masked, public} 매핑.
    사설 IP는 scanner/gateway/lan-host-N 라벨로 마스킹.
    공인 IP도 public-host-N 으로 마스킹 (Cloudflare 뒤 오리진 IP 보호).
    """
    cur.execute("SELECT ip FROM openvas_hosts ORDER BY ip")
    ips = [r["ip"] for r in cur.fetchall()]

    result, lan_counter, pub_counter = {}, 0, 0
    for ip in ips:
        if not _is_private(ip):
            pub_counter += 1
            slug = f"public-host-{pub_counter}"
            result[ip] = {"ip": ip, "slug": slug, "label": slug,
                          "masked": True, "public": True}
            continue
        if ip == SCANNER_IP:
            result[ip] = {"ip": ip, "slug": "scanner",
                          "label": "scanner", "masked": True, "public": False}
        elif ip.endswith(".1"):
            result[ip] = {"ip": ip, "slug": "gateway",
                          "label": "gateway", "masked": True, "public": False}
        else:
            lan_counter += 1
            slug = f"lan-host-{lan_counter}"
            result[ip] = {"ip": ip, "slug": slug, "label": slug,
                          "masked": True, "public": False}
    return result


def _slug_to_ip(ip_map: dict, slug: str) -> str | None:
    for ip, meta in ip_map.items():
        if meta["slug"] == slug:
            return ip
    return None


def _label(ip_map: dict, ip: str) -> str:
    return ip_map.get(ip, {}).get("label", "unknown")


def _slug(ip_map: dict, ip: str) -> str:
    return ip_map.get(ip, {}).get("slug", "unknown")


@openvas_bp.route("/infra", strict_slashes=False)
def infra_dashboard():
    with _db() as conn, conn.cursor() as cur:
        ip_map = _build_ip_map(cur)
        cur.execute("""
            SELECT h.ip, h.os_detected, h.last_seen,
                   COUNT(f.id) AS total,
                   SUM(CASE WHEN f.severity >= 9 THEN 1 ELSE 0 END) AS critical,
                   SUM(CASE WHEN f.severity >= 7 AND f.severity < 9 THEN 1 ELSE 0 END) AS high,
                   SUM(CASE WHEN f.severity >= 4 AND f.severity < 7 THEN 1 ELSE 0 END) AS medium,
                   SUM(CASE WHEN f.severity < 4 THEN 1 ELSE 0 END) AS low,
                   MAX(f.severity) AS worst
            FROM openvas_hosts h
            LEFT JOIN openvas_findings f ON f.host_ip = h.ip
            GROUP BY h.ip, h.os_detected, h.last_seen
            ORDER BY worst DESC, h.ip
        """)
        rows = cur.fetchall()
        hosts = []
        for r in rows:
            meta = ip_map.get(r["ip"], {})
            hosts.append({
                **r,
                "label": meta.get("label", r["ip"]),
                "slug": meta.get("slug", r["ip"]),
                "masked": meta.get("masked", False),
            })

        cur.execute("SELECT COUNT(*) AS n FROM v_my_infrastructure_cves")
        cross_count = cur.fetchone()["n"]

        cur.execute("""
            SELECT scan_ended, finding_count, host_count, severity_max
            FROM openvas_reports
            ORDER BY scan_ended DESC LIMIT 1
        """)
        last_scan = cur.fetchone()

    return render_template("infra.html", hosts=hosts,
                           cross_count=cross_count, last_scan=last_scan)


@openvas_bp.route("/infra/host/<slug>")
def infra_host(slug: str):
    with _db() as conn, conn.cursor() as cur:
        ip_map = _build_ip_map(cur)
        ip = _slug_to_ip(ip_map, slug)
        if not ip:
            abort(404)
        cur.execute("""
            SELECT id, nvt_name, port, severity, threat, cve_ids,
                   ai_priority, ai_status, ingested_at, false_positive, false_positive_reason
            FROM openvas_findings
            WHERE host_ip=%s
            ORDER BY severity DESC, ingested_at DESC
        """, (ip,))
        findings = cur.fetchall()
        cur.execute("SELECT os_detected, last_seen FROM openvas_hosts WHERE ip=%s", (ip,))
        host_row = cur.fetchone()
    if not host_row:
        abort(404)
    host = {**host_row, "label": ip_map[ip]["label"],
            "slug": ip_map[ip]["slug"], "masked": ip_map[ip]["masked"],
            "display_ip": None}
    return render_template("infra_host.html", host=host, findings=findings)


@openvas_bp.route("/infra/finding/<int:fid>")
def infra_finding(fid: int):
    with _db() as conn, conn.cursor() as cur:
        ip_map = _build_ip_map(cur)
        cur.execute("SELECT * FROM openvas_findings WHERE id=%s", (fid,))
        f = cur.fetchone()
    if not f:
        abort(404)
    meta = ip_map.get(f["host_ip"], {})
    f = {**f,
         "host_label": meta.get("label", "unknown"),
         "host_slug": meta.get("slug", "unknown"),
         "display_ip": None}
    return render_template("infra_finding.html", f=f)


@openvas_bp.route("/infra/api/summary")
def infra_api_summary():
    with _db() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(DISTINCT host_ip) AS hosts,
                   COUNT(*) AS findings,
                   SUM(severity >= 9)                          AS critical,
                   SUM(severity >= 7 AND severity < 9)         AS high,
                   SUM(severity >= 4 AND severity < 7)         AS medium,
                   SUM(severity > 0 AND severity < 4)          AS low,
                   COALESCE(MAX(severity), 0)                  AS severity_max,
                   COALESCE(AVG(severity), 0)                  AS avg_severity
            FROM openvas_findings
        """)
        agg = cur.fetchone() or {}
        cur.execute("""
            SELECT scan_ended, finding_count, host_count, severity_max
            FROM openvas_reports
            ORDER BY scan_ended DESC LIMIT 1
        """)
        last = cur.fetchone() or {}
    def _int(v): return int(v or 0)
    critical = _int(agg.get("critical"))
    high = _int(agg.get("high"))
    return jsonify({
        "hosts": _int(agg.get("hosts")),
        "findings": _int(agg.get("findings")),
        "critical": critical,
        "high": high,
        "medium": _int(agg.get("medium")),
        "low": _int(agg.get("low")),
        "urgent": critical + high,
        "severity_max": float(agg.get("severity_max") or 0),
        "avg_severity": round(float(agg.get("avg_severity") or 0), 2),
        "last_scan_ended": last.get("scan_ended").isoformat() if last.get("scan_ended") else None,
    })


@openvas_bp.route("/infra/map")
def infra_map():
    return render_template("infra_map.html")


@openvas_bp.route("/infra/api/map")
def infra_api_map():
    """네트워크 맵용 노드/엣지 (IP 마스킹 적용).
    - 노드 id/label 은 slug, 실제 IP 는 응답에 포함하지 않음
    - 엣지는 같은 /24 내 gateway(.1) 허브
    """
    with _db() as conn, conn.cursor() as cur:
        ip_map = _build_ip_map(cur)
        cur.execute("""
            SELECT h.ip, h.os_detected,
                   COUNT(f.id) AS finding_count,
                   COALESCE(MAX(f.severity), 0) AS severity_max,
                   SUM(CASE WHEN f.severity >= 7 THEN 1 ELSE 0 END) AS high_count,
                   GROUP_CONCAT(DISTINCT f.port ORDER BY f.port SEPARATOR ',') AS ports
            FROM openvas_hosts h
            LEFT JOIN openvas_findings f ON f.host_ip = h.ip
            GROUP BY h.ip, h.os_detected
        """)
        rows = cur.fetchall()

    nodes, links = [], []
    subnets = {}

    for r in rows:
        ip = r["ip"]
        meta = ip_map.get(ip, {})
        slug = meta.get("slug", ip)
        is_scanner = (ip == SCANNER_IP)
        is_gateway = ip.endswith(".1") and ip.startswith("192.168.")
        role = "scanner" if is_scanner else ("gateway" if is_gateway else "host")
        group = "external" if meta.get("public") else "lan"
        ports_list = []
        if r["ports"]:
            ports_list = sorted(set(p for p in r["ports"].split(",") if p))[:8]
        nodes.append({
            "id": slug,
            "label": meta.get("label", slug),
            "os": r["os_detected"] or "",
            "role": role,
            "group": group,
            "findings": int(r["finding_count"] or 0),
            "severity": float(r["severity_max"] or 0),
            "high_count": int(r["high_count"] or 0),
            "ports": ports_list,
            "public": meta.get("public", False),
        })
        subnet = ".".join(ip.split(".")[:3]) if ":" not in ip else "ipv6"
        subnets.setdefault(subnet, []).append(ip)

    # LAN 서브넷: gateway 허브 중심으로 연결
    for subnet, ips in subnets.items():
        if not subnet.startswith("192.168"):
            continue
        hub_ip = next((ip for ip in ips if ip.endswith(".1")), None) \
            or next((ip for ip in ips if ip == SCANNER_IP), None) \
            or ips[0]
        hub_slug = ip_map[hub_ip]["slug"]
        for ip in ips:
            if ip == hub_ip:
                continue
            links.append({"source": hub_slug,
                          "target": ip_map[ip]["slug"], "kind": "lan"})
        if hub_ip != SCANNER_IP and SCANNER_IP in ips:
            links.append({"source": ip_map[SCANNER_IP]["slug"],
                          "target": hub_slug, "kind": "uplink"})

    # 공인(external) 호스트: gateway → public-host 링크로 연결 (없으면 scanner에서)
    gateway_slug = next(
        (m["slug"] for ip, m in ip_map.items()
         if ip.startswith("192.168.") and ip.endswith(".1")),
        None,
    ) or next(
        (m["slug"] for ip, m in ip_map.items() if ip == SCANNER_IP),
        None,
    )
    for ip, meta in ip_map.items():
        if not meta.get("public"):
            continue
        if gateway_slug:
            links.append({"source": gateway_slug,
                          "target": meta["slug"], "kind": "wan"})

    return jsonify({"nodes": nodes, "links": links})
