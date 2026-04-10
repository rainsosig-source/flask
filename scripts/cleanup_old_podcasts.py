"""
2주 이상된 팟캐스트 파일 자동 정리 스크립트
- MP3 파일 삭제 (로컬 또는 SFTP)
- DB 레코드 삭제 (episodes 테이블)

사용법:
    python scripts/cleanup_old_podcasts.py          # SFTP로 원격 삭제
    python scripts/cleanup_old_podcasts.py --local  # 서버에서 직접 실행 시
    python scripts/cleanup_old_podcasts.py --dry-run  # 테스트 (삭제 안 함)
"""

import os
import sys
import argparse
import pymysql
from datetime import datetime
from dotenv import load_dotenv

# .env 로드 (스크립트 디렉토리 또는 상위 디렉토리)
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, '.env')
if not os.path.exists(env_path):
    env_path = os.path.join(os.path.dirname(script_dir), '.env')
load_dotenv(env_path)

# 설정
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "podcast")

# SFTP 설정 (원격 실행 시)
SFTP_HOST = os.getenv("SFTP_HOST", "")
SFTP_PORT = int(os.getenv("SFTP_PORT", "22"))
SFTP_USER = os.getenv("SFTP_USER", "")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD", "")

# 보관 기간 (일)
RETENTION_DAYS = 14


def get_db_connection():
    """DB 연결"""
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        db=DB_NAME,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


def get_old_episodes(cursor, days):
    """보관 기간이 지난 에피소드 조회"""
    query = """
        SELECT id, title, mp3_path, created_at 
        FROM episodes 
        WHERE created_at < NOW() - INTERVAL %s DAY
        ORDER BY created_at ASC
    """
    cursor.execute(query, (days,))
    return cursor.fetchall()


def delete_local_file(file_path):
    """로컬 파일 삭제"""
    try:
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            os.remove(file_path)
            return True, file_size
        return True, 0
    except Exception as e:
        print(f"   ⚠️ 파일 삭제 실패: {e}")
        return False, 0


def delete_remote_file(sftp, file_path):
    """SFTP로 원격 파일 삭제"""
    try:
        stat = sftp.stat(file_path)
        file_size = stat.st_size
        sftp.remove(file_path)
        return True, file_size
    except FileNotFoundError:
        return True, 0
    except Exception as e:
        print(f"   ⚠️ 파일 삭제 실패: {e}")
        return False, 0


def cleanup_empty_directories_local(base_path="/root/flask-app/static/podcast"):
    """빈 디렉토리 정리 (로컬)"""
    try:
        for root, dirs, files in os.walk(base_path, topdown=False):
            for d in dirs:
                dir_path = os.path.join(root, d)
                if os.path.isdir(dir_path) and not os.listdir(dir_path):
                    os.rmdir(dir_path)
        print("📁 빈 디렉토리 정리 완료")
    except Exception as e:
        print(f"⚠️ 디렉토리 정리 중 오류: {e}")


def cleanup_empty_directories_remote(ssh, base_path="/root/flask-app/static/podcast"):
    """빈 디렉토리 정리 (원격)"""
    try:
        command = f"find {base_path} -type d -empty -delete 2>/dev/null"
        ssh.exec_command(command)
        print("📁 빈 디렉토리 정리 완료")
    except Exception as e:
        print(f"⚠️ 디렉토리 정리 중 오류: {e}")


def cleanup_old_podcasts(dry_run=False, limit=None, local_mode=False, days=14):
    """메인 정리 함수"""
    print("=" * 70)
    print(f"🧹 팟캐스트 정리 스크립트 ({'DRY-RUN' if dry_run else '실제 삭제'}) [{'로컬' if local_mode else 'SFTP'}]")
    print(f"📅 보관 기간: {days}일")
    print(f"🕐 실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    sftp = None
    ssh = None
    
    # SFTP 연결 (원격 모드일 때만)
    if not local_mode and not dry_run:
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f"\n🔗 서버 연결 중: {SFTP_HOST}")
        ssh.connect(SFTP_HOST, SFTP_PORT, SFTP_USER, SFTP_PASSWORD)
        sftp = ssh.open_sftp()
    
    try:
        # DB 연결
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 삭제 대상 조회
        old_episodes = get_old_episodes(cursor, days)
        
        if limit:
            old_episodes = old_episodes[:limit]
        
        print(f"\n📊 삭제 대상: {len(old_episodes)}개 에피소드")
        
        if not old_episodes:
            print("✅ 삭제할 에피소드가 없습니다.")
            return
        
        deleted_files = 0
        deleted_db = 0
        total_size = 0
        
        print("\n" + "-" * 70)
        
        for ep in old_episodes:
            ep_id = ep['id']
            title = (ep['title'][:40] if ep['title'] else "제목 없음")
            mp3_path = ep['mp3_path']
            created_at = ep['created_at']
            
            print(f"\n[{ep_id}] {title}...")
            print(f"   📅 생성: {created_at}")
            print(f"   📂 파일: {mp3_path}")
            
            if dry_run:
                print("   ⏭️ DRY-RUN: 건너뜀")
                deleted_files += 1
                deleted_db += 1
                continue
            
            # 1. 파일 삭제
            if mp3_path:
                if local_mode:
                    success, size = delete_local_file(mp3_path)
                else:
                    success, size = delete_remote_file(sftp, mp3_path)
                
                if success:
                    deleted_files += 1
                    total_size += size
                    print("   ✅ 파일 삭제 완료")
            
            # 2. DB 레코드 삭제
            try:
                cursor.execute("DELETE FROM episodes WHERE id = %s", (ep_id,))
                deleted_db += 1
                print("   ✅ DB 레코드 삭제 완료")
            except Exception as e:
                print(f"   ❌ DB 삭제 실패: {e}")
        
        # 커밋
        if not dry_run:
            conn.commit()
            if local_mode:
                cleanup_empty_directories_local()
            elif ssh:
                cleanup_empty_directories_remote(ssh)
        
        # 결과 출력
        print("\n" + "=" * 70)
        print("📊 정리 결과")
        print("=" * 70)
        print(f"   삭제된 파일: {deleted_files}개")
        print(f"   삭제된 DB 레코드: {deleted_db}개")
        print(f"   확보된 용량: {total_size / (1024*1024):.1f}MB")
        print("=" * 70)
        
        cursor.close()
        conn.close()
        
    finally:
        if sftp:
            sftp.close()
        if ssh:
            ssh.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="2주 이상된 팟캐스트 정리")
    parser.add_argument("--dry-run", action="store_true", help="테스트 실행 (실제 삭제 안 함)")
    parser.add_argument("--local", action="store_true", help="서버에서 직접 실행 시 (로컬 파일 삭제)")
    parser.add_argument("--limit", type=int, default=None, help="삭제할 최대 개수 제한")
    parser.add_argument("--days", type=int, default=RETENTION_DAYS, help=f"보관 기간 (기본값: {RETENTION_DAYS}일)")
    
    args = parser.parse_args()
    
    cleanup_old_podcasts(
        dry_run=args.dry_run, 
        limit=args.limit, 
        local_mode=args.local,
        days=args.days
    )
