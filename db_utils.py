# ==============================================================================
# 웹 모듈 공통 데이터베이스 유틸리티
# DB 연결은 database.py의 커넥션 풀을 사용
# ==============================================================================

from database import get_cve_db as get_db

# ==============================================================================
# 심각도 관련 유틸리티 함수
# ==============================================================================
SEVERITY_COLORS = {
    'CRITICAL': '#dc3545',
    'HIGH': '#fd7e14',
    'MEDIUM': '#ffc107',
    'LOW': '#17a2b8',
    'UNKNOWN': '#6c757d'
}

SEVERITY_BADGES = {
    'CRITICAL': 'badge-critical',
    'HIGH': 'badge-high',
    'MEDIUM': 'badge-medium',
    'LOW': 'badge-low',
    'UNKNOWN': 'badge-unknown'
}


def severity_color(severity: str) -> str:
    """심각도별 색상 반환"""
    return SEVERITY_COLORS.get(severity, '#6c757d')


def severity_badge(severity: str) -> str:
    """심각도 배지 클래스"""
    return SEVERITY_BADGES.get(severity, 'badge-unknown')


# ==============================================================================
# 소프트웨어 탐지 명령어 매핑
# ==============================================================================
DETECT_COMMANDS = {
    'Apache HTTP Server': ['httpd -v', 'apache2 -v'],
    'Nginx': ['nginx -v'],
    'MySQL': ['mysql --version'],
    'MariaDB': ['mariadb --version', 'mysql --version'],
    'PostgreSQL': ['psql --version'],
    'PHP': ['php -v'],
    'Apache Tomcat': ['catalina.sh version', '/opt/tomcat/bin/version.sh'],
    'Redis': ['redis-server --version'],
    'OpenSSH': ['ssh -V'],
    'OpenSSL': ['openssl version'],
    'Sudo': ['sudo --version'],
    'Bash': ['bash --version'],
    'Docker': ['docker --version'],
    'Kubernetes': ['kubectl version --client'],
    'Node.js': ['node --version'],
    'Spring Framework': [],
    'Python': ['python3 --version', 'python --version'],
    'Jenkins': [],
    'Git': ['git --version']
}
