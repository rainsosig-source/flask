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
            
        conn.close()
        return render_template('vuln_detail.html', cve=cve)
        
    except Exception as e:
        return render_template('vuln_detail.html', cve=None, error=str(e))


@vuln_bp.route('/search')
def search():
    """CVE 검색"""
    query = request.args.get('q', '')
    results = []
    
    if query:
        try:
            conn = get_cve_db()
            with conn.cursor() as cursor:
                search_query = f"%{query}%"
                cursor.execute("""
                    SELECT e.*, GROUP_CONCAT(DISTINCT t.name) as software
                    FROM cve_entries e
                    LEFT JOIN cve_software s ON e.cve_id = s.cve_id
                    LEFT JOIN monitor_targets t ON s.target_id = t.id
                    WHERE e.cve_id LIKE %s
                       OR e.description_en LIKE %s
                       OR e.description_ko LIKE %s
                       OR e.simple_explanation LIKE %s
                       OR t.name LIKE %s
                    GROUP BY e.id
                    ORDER BY e.cvss_score DESC
                    LIMIT 50
                """, (search_query, search_query, search_query, search_query, search_query))
                results = cursor.fetchall()
            conn.close()
        except Exception as e:
            return render_template('vuln_search.html', query=query, results=[], error=str(e))
    
    return render_template('vuln_search.html', query=query, results=results)


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


@vuln_bp.route('/client')
def client_page():
    """클라이언트 다운로드 및 사용법 페이지"""
    return render_template('vuln_client.html')

