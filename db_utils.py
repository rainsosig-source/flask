# ==============================================================================
# 웹 모듈 공통 데이터베이스 유틸리티
# DB 연결 및 공통 함수를 한 곳에서 관리
# ==============================================================================

import os
import pymysql
from functools import lru_cache

# .env 파일 로드 (현재 디렉토리 또는 상위 디렉토리)
from dotenv import load_dotenv

# 현재 디렉토리와 상위 디렉토리 모두에서 .env 파일 검색
_current_dir = os.path.dirname(os.path.abspath(__file__))
_env_path = os.path.join(_current_dir, '.env')
if not os.path.exists(_env_path):
    _env_path = os.path.join(os.path.dirname(_current_dir), '.env')
load_dotenv(_env_path)


# ==============================================================================
# 데이터베이스 설정 (CVE 전용 환경변수에서 로드)
# podcast DB와 충돌 방지를 위해 CVE_ 접두사 사용
# ==============================================================================
CVE_DB_HOST = os.environ.get('CVE_DB_HOST', 'localhost')
CVE_DB_USER = os.environ.get('CVE_DB_USER', 'root')
CVE_DB_PASS = os.environ.get('CVE_DB_PASS', '')
CVE_DB_NAME = os.environ.get('CVE_DB_NAME', 'cve_monitor')
CVE_DB_PORT = int(os.environ.get('CVE_DB_PORT', '3306'))


def get_db():
    """CVE 데이터베이스 연결"""
    return pymysql.connect(
        host=CVE_DB_HOST,
        user=CVE_DB_USER,
        password=CVE_DB_PASS,
        database=CVE_DB_NAME,
        port=CVE_DB_PORT,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


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
    'Spring Framework': [],  # JAR 파일 검사 필요
    'Python': ['python3 --version', 'python --version'],
    'Jenkins': [],  # WAR 파일 버전 확인 필요
    'Git': ['git --version']
}
