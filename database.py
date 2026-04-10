# ==============================================================================
# 통합 데이터베이스 커넥션 풀 관리
# Podcast DB와 CVE DB 모두 PooledDB로 관리
# ==============================================================================

import os
import pymysql
from dbutils.pooled_db import PooledDB

# Podcast DB
_podcast_pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=2,
    maxcached=5,
    host=os.environ.get('DB_HOST', 'localhost'),
    user=os.environ.get('DB_USER', 'root'),
    password=os.environ.get('DB_PASS', ''),
    db=os.environ.get('DB_NAME', 'podcast'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

# CVE DB
_cve_pool = PooledDB(
    creator=pymysql,
    maxconnections=10,
    mincached=2,
    maxcached=5,
    host=os.environ.get('CVE_DB_HOST', 'localhost'),
    user=os.environ.get('CVE_DB_USER', 'root'),
    password=os.environ.get('CVE_DB_PASS', ''),
    db=os.environ.get('CVE_DB_NAME', 'cve_monitor'),
    port=int(os.environ.get('CVE_DB_PORT', '3306')),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)


def get_db_connection():
    """Podcast DB 커넥션 (풀에서 가져옴)"""
    return _podcast_pool.connection()


def get_cve_db():
    """CVE DB 커넥션 (풀에서 가져옴)"""
    return _cve_pool.connection()
