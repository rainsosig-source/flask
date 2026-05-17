"""
CVE 취약점 웹 인터페이스 - Flask Blueprint
sosig.shop/vuln 경로에서 CVE 정보를 표시
"""

from flask import Blueprint, render_template, request, jsonify

# 공통 DB 유틸리티 모듈 사용
from db_utils import get_db as get_cve_db, severity_color, severity_badge

vuln_bp = Blueprint('vuln', __name__, url_prefix='/vuln')


@vuln_bp.route('/')
def index():
    """대시보드 - 프로그램별 CVE 현황"""
    try:
        conn = get_cve_db()
        with conn.cursor() as cursor:
            # 전체 통계
            stats = {}
            
            # 총 CVE 수
            cursor.execute("SELECT COUNT(*) as total FROM cve_entries")
            stats['total_cves'] = cursor.fetchone()['total']
            
            # 심각도별 개수
            cursor.execute("""
                SELECT severity, COUNT(*) as count 
                FROM cve_entries 
                GROUP BY severity
            """)
            stats['by_severity'] = {row['severity']: row['count'] for row in cursor.fetchall()}
            
            # AI 분석 완료율
            cursor.execute("SELECT COUNT(*) as count FROM cve_entries WHERE ai_analyzed = TRUE")
            stats['analyzed'] = cursor.fetchone()['count']
            
            # 프로그램(소프트웨어)별 통계
            cursor.execute("""
                SELECT 
                    t.id,
                    t.name,
                    COUNT(DISTINCT s.cve_id) as cve_count,
                    SUM(CASE WHEN e.severity = 'CRITICAL' THEN 1 ELSE 0 END) as critical_count,
                    SUM(CASE WHEN e.severity = 'HIGH' THEN 1 ELSE 0 END) as high_count,
                    SUM(CASE WHEN e.severity = 'MEDIUM' THEN 1 ELSE 0 END) as medium_count,
                    SUM(CASE WHEN e.severity = 'LOW' THEN 1 ELSE 0 END) as low_count,
                    MAX(e.cvss_score) as max_cvss,
                    MAX(e.published_date) as latest_date
                FROM monitor_targets t
                LEFT JOIN cve_software s ON t.id = s.target_id
                LEFT JOIN cve_entries e ON s.cve_id = e.cve_id
                WHERE t.enabled = 1
                GROUP BY t.id, t.name
                ORDER BY cve_count DESC, t.name
            """)
            software_stats = cursor.fetchall()
            
            # 최근 CVE 10개 (간략히)
            cursor.execute("""
                SELECT e.cve_id, e.severity, e.cvss_score, e.published_date,
                       GROUP_CONCAT(DISTINCT t.name) as software
                FROM cve_entries e
                LEFT JOIN cve_software s ON e.cve_id = s.cve_id
                LEFT JOIN monitor_targets t ON s.target_id = t.id
                GROUP BY e.id
                ORDER BY e.published_date DESC 
                LIMIT 10
            """)
            recent_cves = cursor.fetchall()
            
        conn.close()
        return render_template('vuln.html', stats=stats, software_stats=software_stats, recent_cves=recent_cves)
        
    except Exception as e:
        return render_template('vuln.html', stats={}, software_stats=[], recent_cves=[], error=str(e))


@vuln_bp.route('/list')
def cve_list():
    """CVE 목록 (페이지네이션, 필터링)"""
    page = request.args.get('page', 1, type=int)
    severity = request.args.get('severity', None)
    software = request.args.get('software', None)
    date_from = request.args.get('date_from', None)
    date_to = request.args.get('date_to', None)
    sort_by = request.args.get('sort_by', 'cvss_score')  # cvss_score, published_date, cve_id
    sort_order = request.args.get('sort_order', 'desc')  # asc, desc
    per_page = 30
    
    # 허용된 정렬 필드만 사용
    allowed_sort_fields = {'cvss_score': 'e.cvss_score', 'published_date': 'e.published_date', 'cve_id': 'e.cve_id', 'last_modified': 'e.last_modified'}
    sort_column = allowed_sort_fields.get(sort_by, 'e.cvss_score')
    sort_direction = 'ASC' if sort_order == 'asc' else 'DESC'
    
    try:
        conn = get_cve_db()
        with conn.cursor() as cursor:
            # 소프트웨어 목록 가져오기
            cursor.execute("SELECT id, name FROM monitor_targets WHERE enabled = 1 ORDER BY name")
            software_list = cursor.fetchall()
            
            # 조건 생성
            where_clause = "1=1"
            params = []
            
            if severity:
                where_clause += " AND e.severity = %s"
                params.append(severity)
            
            if software:
                where_clause += " AND t.id = %s"
                params.append(software)
            
            if date_from:
                where_clause += " AND e.published_date >= %s"
                params.append(date_from)
            
            if date_to:
                where_clause += " AND e.published_date <= %s"
                params.append(date_to + " 23:59:59")
            
            # 총 개수
            cursor.execute(f"""
                SELECT COUNT(DISTINCT e.id) as total
                FROM cve_entries e
                LEFT JOIN cve_software s ON e.cve_id = s.cve_id
                LEFT JOIN monitor_targets t ON s.target_id = t.id
                WHERE {where_clause}
            """, params)
            total = cursor.fetchone()['total']
            
            # 데이터 조회
            offset = (page - 1) * per_page
            cursor.execute(f"""
                SELECT e.*, GROUP_CONCAT(DISTINCT t.name) as software
                FROM cve_entries e
                LEFT JOIN cve_software s ON e.cve_id = s.cve_id
                LEFT JOIN monitor_targets t ON s.target_id = t.id
                WHERE {where_clause}
                GROUP BY e.id
                ORDER BY {sort_column} {sort_direction}, e.published_date DESC
                LIMIT %s OFFSET %s
            """, params + [per_page, offset])
            cves = cursor.fetchall()
            
        conn.close()
        
        total_pages = (total + per_page - 1) // per_page
        
        return render_template('vuln_list.html', 
                               cves=cves,
                               page=page,
                               total_pages=total_pages,
                               total=total,
                               current_severity=severity,
                               current_software=software,
                               software_list=software_list,
                               date_from=date_from,
                               date_to=date_to,
                               sort_by=sort_by,
                               sort_order=sort_order)
        
    except Exception as e:
        return render_template('vuln_list.html', cves=[], page=1, total_pages=0, total=0, 
                               software_list=[], error=str(e))


@vuln_bp.route('/cve/<cve_id>')
def cve_detail(cve_id):
    """CVE 상세 정보"""
    try:
        conn = get_cve_db()
        with conn.cursor() as cursor:
            # CVE 정보
            cursor.execute("SELECT * FROM cve_entries WHERE cve_id = %s", (cve_id,))
            cve = cursor.fetchone()
            
            if not cve:
                conn.close()
                return render_template('vuln_detail.html', cve=None, error=f"CVE {cve_id}를 찾을 수 없습니다.")
            
            # 소프트웨어 목록
            cursor.execute("""
                SELECT t.name as software_name
                FROM cve_software s
                JOIN monitor_targets t ON s.target_id = t.id
                WHERE s.cve_id = %s
            """, (cve_id,))
            cve['software_list'] = cursor.fetchall()
            
            # 참조 링크
            cursor.execute("""
                SELECT url, source 
                FROM cve_references 
                WHERE cve_id = %s
            """, (cve_id,))
            cve['references'] = cursor.fetchall()

            # KISA 권고 (cve_id 매칭)
            try:
                import json as _json
                cursor.execute("""
                    SELECT id, title, link, posted_at, ai_summary, ai_priority, category
                    FROM kisa_advisories
                    WHERE JSON_CONTAINS(cve_ids, %s)
                    ORDER BY posted_at DESC
                    LIMIT 5
                """, (_json.dumps(cve_id),))
                cve['kisa_advisories'] = cursor.fetchall()
            except Exception as _e:
                cve['kisa_advisories'] = []

            # OpenVAS stub CVE인 경우 finding 정보로 보완
            if cve.get('source_flags') and 'openvas' in str(cve['source_flags']):
                cursor.execute("""
                    SELECT f.nvt_name, f.description, f.solution,
                           f.severity, f.host_ip, f.port, f.threat
                    FROM openvas_findings f
                    JOIN openvas_finding_cves ofc ON ofc.finding_id = f.id
                    WHERE ofc.cve_id = %s
                    ORDER BY f.severity DESC
                    LIMIT 5
                """, (cve_id,))
                cve['openvas_findings'] = cursor.fetchall()
            else:
                cve['openvas_findings'] = []

        conn.close()
        return render_template('vuln_detail.html', cve=cve)
        
    except Exception as e:
        return render_template('vuln_detail.html', cve=None, error=str(e))


@vuln_bp.route('/search')
def search():
    """CVE 검색 (NVD + OpenVAS 통합)"""
    query = request.args.get('q', '')
    infra_only = request.args.get('infra_only', '0') == '1'
    results = []

    if query:
        try:
            conn = get_cve_db()
            with conn.cursor() as cursor:
                search_query = f"%{query}%"
                infra_clause = "AND FIND_IN_SET('openvas', e.source_flags)" if infra_only else ""
                cursor.execute(f"""
                    SELECT e.*,
                           GROUP_CONCAT(DISTINCT t.name) as software,
                           FIND_IN_SET('openvas', e.source_flags) as in_my_infra
                    FROM cve_entries e
                    LEFT JOIN cve_software s ON e.cve_id = s.cve_id
                    LEFT JOIN monitor_targets t ON s.target_id = t.id
                    WHERE (
                        e.cve_id LIKE %s
                       OR e.description_en LIKE %s
                       OR e.description_ko LIKE %s
                       OR e.simple_explanation LIKE %s
                       OR t.name LIKE %s
                    ) {infra_clause}
                    GROUP BY e.id
                    ORDER BY COALESCE(e.cvss_score, e.openvas_severity, 0) DESC
                    LIMIT 50
                """, (search_query, search_query, search_query, search_query, search_query))
                results = cursor.fetchall()
            conn.close()
        except Exception as e:
            return render_template('vuln_search.html', query=query, results=[],
                                   infra_only=infra_only, error=str(e))

    return render_template('vuln_search.html', query=query, results=results,
                           infra_only=infra_only)


@vuln_bp.route('/api/stats')
def api_stats():
    """통계 API (JSON)"""
    try:
        conn = get_cve_db()
        with conn.cursor() as cursor:
            stats = {}
            
            cursor.execute("SELECT COUNT(*) as total FROM cve_entries")
            stats['total_cves'] = cursor.fetchone()['total']
            
            cursor.execute("""
                SELECT severity, COUNT(*) as count 
                FROM cve_entries 
                GROUP BY severity
            """)
            stats['by_severity'] = {row['severity']: row['count'] for row in cursor.fetchall()}
            
            cursor.execute("SELECT COUNT(*) as count FROM cve_entries WHERE ai_analyzed = TRUE")
            stats['analyzed'] = cursor.fetchone()['count']
            
        conn.close()
        return jsonify(stats)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@vuln_bp.route('/about')
def about():
    """취약점 모니터링 구조·데이터 흐름 소개."""
    stats = {
        "cve_total": 0,
        "cve_recent_30d": 0,
        "cve_analyzed": 0,
        "by_severity": {},
        "hosts": 0,
        "findings": 0,
        "findings_by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "last_scan": None,
    }
    try:
        conn = get_cve_db()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM cve_entries")
            stats["cve_total"] = cur.fetchone()["n"]

            cur.execute(
                "SELECT COUNT(*) AS n FROM cve_entries "
                "WHERE published_date >= DATE_SUB(NOW(), INTERVAL 30 DAY)"
            )
            stats["cve_recent_30d"] = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM cve_entries WHERE ai_analyzed = TRUE")
            stats["cve_analyzed"] = cur.fetchone()["n"]

            cur.execute(
                "SELECT severity, COUNT(*) AS n FROM cve_entries "
                "GROUP BY severity"
            )
            stats["by_severity"] = {r["severity"]: r["n"] for r in cur.fetchall()}

            # OpenVAS 집계
            cur.execute("SELECT COUNT(*) AS n FROM openvas_hosts")
            stats["hosts"] = cur.fetchone()["n"]

            cur.execute("""
                SELECT
                    SUM(severity >= 9)                   AS critical,
                    SUM(severity >= 7 AND severity < 9)  AS high,
                    SUM(severity >= 4 AND severity < 7)  AS medium,
                    SUM(severity > 0 AND severity < 4)   AS low,
                    COUNT(*)                             AS total
                FROM openvas_findings
            """)
            row = cur.fetchone() or {}
            stats["findings"] = int(row.get("total") or 0)
            stats["findings_by_severity"] = {
                "critical": int(row.get("critical") or 0),
                "high":     int(row.get("high") or 0),
                "medium":   int(row.get("medium") or 0),
                "low":      int(row.get("low") or 0),
            }

            cur.execute(
                "SELECT MAX(scan_ended) AS t FROM openvas_reports"
            )
            t = cur.fetchone()["t"]
            if t:
                stats["last_scan"] = t.strftime("%Y-%m-%d %H:%M")
        conn.close()
    except Exception as e:
        stats["error"] = str(e)

    return render_template("vuln_about.html", stats=stats)
