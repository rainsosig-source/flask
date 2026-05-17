#!/usr/bin/env python3
# 시간대별/일별 모음 팟캐스트 7일 retention 정리

import os
from datetime import datetime
from pathlib import Path

ROOTS = [
    "/root/flask-app/static/podcast/hourly",
    "/root/flask-app/static/podcast/daily",
]
RETENTION_DAYS = 7


def main():
    cutoff = datetime.now().timestamp() - RETENTION_DAYS * 86400
    removed = 0
    for root in ROOTS:
        if not os.path.exists(root):
            continue
        for path in Path(root).rglob("*.mp3"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
                    print(f"removed: {path}")
            except Exception as e:
                print(f"error: {path}: {e}")
        # 빈 디렉토리 정리 (역순으로 자식부터)
        for path in sorted(Path(root).rglob("*"), reverse=True):
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except Exception:
                pass
    print(f"[{datetime.now().isoformat()}] retention 정리: {removed}개 삭제")


if __name__ == "__main__":
    main()
