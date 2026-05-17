#!/usr/bin/env python3
"""보안 대시보드에 나오는 IP들의 국가 정보를 GeoIP 캐시에 채운다.

기존 tcp_traceroute.py 스키마와 호환: geoip(ip, lat, lon, country, cached_at).
rate limit: 요청 간 0.3초 대기.
"""
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta

CACHE_DB = "/opt/flask-app/cache/geoip.db"
NGINX_LOG = "/var/log/nginx/access.log"
AUTH_LOG = "/var/log/auth.log"
API_URL = "https://ipwho.is/{}"
API_TIMEOUT = 2.0
RATE_SLEEP = 0.3
TTL_DAYS = 30


def _is_private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


def _cached_recent(conn, ip):
    row = conn.execute("SELECT cached_at FROM geoip WHERE ip=?", (ip,)).fetchone()
    if not row:
        return False
    return (time.time() - row[0]) < TTL_DAYS * 86400


def _collect_ips():
    ips = set()
    cutoff_iso = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")

    if os.path.exists(NGINX_LOG):
        try:
            with open(NGINX_LOG, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-50000:]
            nginx_re = re.compile(r"^(\S+)")
            counter = Counter()
            for line in lines:
                m = nginx_re.match(line)
                if m:
                    counter[m.group(1)] += 1
            for ip, _ in counter.most_common(100):
                ips.add(ip)
        except OSError:
            pass

    if os.path.exists(AUTH_LOG):
        try:
            with open(AUTH_LOG, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line[:19] < cutoff_iso:
                        continue
                    for m in re.finditer(r"from (\d+\.\d+\.\d+\.\d+)", line):
                        ips.add(m.group(1))
        except OSError:
            pass

    try:
        out = subprocess.run(
            ["sudo", "-n", "/usr/bin/fail2ban-client", "status", "sshd"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            if "Banned IP list:" in line:
                for ip in line.split(":", 1)[1].split():
                    ips.add(ip)
    except Exception:
        pass

    return {ip for ip in ips if not _is_private(ip)}


def _fetch_and_cache(conn, ip):
    try:
        with urllib.request.urlopen(API_URL.format(ip), timeout=API_TIMEOUT) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return False
    if not data.get("success", True):
        return False
    conn.execute(
        "INSERT OR REPLACE INTO geoip(ip, lat, lon, country, cached_at) VALUES(?,?,?,?,?)",
        (ip, data.get("latitude"), data.get("longitude"),
         data.get("country"), int(time.time())),
    )
    conn.commit()
    return True


def main():
    conn = sqlite3.connect(CACHE_DB, timeout=5)
    ips = _collect_ips()
    print(f"수집된 공인 IP: {len(ips)}")

    to_fetch = [ip for ip in ips if not _cached_recent(conn, ip)]
    print(f"미캐시 IP: {len(to_fetch)}")

    hit = 0
    for i, ip in enumerate(to_fetch, 1):
        if _fetch_and_cache(conn, ip):
            hit += 1
        if i % 20 == 0:
            print(f"  진행 {i}/{len(to_fetch)} (성공 {hit})")
        time.sleep(RATE_SLEEP)
    conn.close()
    print(f"완료: {hit}/{len(to_fetch)} 성공")


if __name__ == "__main__":
    main()
