#!/usr/bin/env python3
"""Scapy 기반 간결 Traceroute.

이전 1,208줄의 raw socket 자체 구현을 scapy의 sr() 기반으로 재작성.
특징:
- sr()가 IP ID / 포트 / seq를 자동 매칭 — 병렬 프로빙 race 없음
- TCP(주+폴백포트) → UDP → ICMP 3-way 폴백 로직
- max_hops 전체를 한 batch로 보내고 매칭 → 빠르고 정확
- 지오로케이션·IATA 힌트·SQLite 캐시 유지 (이전 로직 이식)
- JSON 출력 스키마 동일 (route_blueprint.py 호환)
"""
import argparse
import ipaddress
import json
import os
import re
import socket
import sqlite3
import struct
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

# scapy 로딩 전에 verb을 줄여 stderr 오염 방지
os.environ.setdefault("SCAPY_USE_PCAPDNET", "0")
from scapy.all import IP, TCP, UDP, ICMP, sr, conf, Raw  # type: ignore

conf.verb = 0  # scapy silent

# ============================================================================
# 설정
# ============================================================================

DEFAULT_PORT = 80
DEFAULT_MAX_HOPS = 30
DEFAULT_TIMEOUT = 2.0
DEFAULT_PROBES = 3
UDP_BASE_PORT = 33434
SPORT_BASE = 40000
ICMP_ID_BASE = (os.getpid() & 0xFF) << 8

GEOLOCATION_API_URL = "https://ipwho.is/{ip}"
GEOLOCATION_TIMEOUT = 1.5
GEOIP_CACHE_DB = os.environ.get(
    "TRACEROUTE_GEOIP_CACHE",
    "/opt/flask-app/cache/geoip.db",
)
GEOIP_CACHE_TTL = 30 * 86400
GEOIP_CACHE_NULL_TTL = 3600

PROTOCOL_TCP = "tcp"
PROTOCOL_UDP = "udp"
PROTOCOL_ICMP = "icmp"
PROTOCOL_BOTH = "both"   # TCP + UDP
PROTOCOL_ALL = "all"     # TCP + UDP + ICMP

IATA_HINTS: Dict[str, Tuple[float, float]] = {
    "icn": (37.4602, 126.4407), "gmp": (37.5583, 126.7906),
    "nrt": (35.7647, 140.3863), "hnd": (35.5494, 139.7798),
    "kix": (34.4347, 135.2440), "hkg": (22.3080, 113.9185),
    "tpe": (25.0777, 121.2328), "sin": (1.3644, 103.9915),
    "bkk": (13.6900, 100.7501), "del": (28.5562, 77.1000),
    "bom": (19.0896, 72.8656), "dxb": (25.2532, 55.3657),
    "lhr": (51.4700, -0.4543), "ams": (52.3105, 4.7683),
    "cdg": (49.0097, 2.5479), "fra": (50.0379, 8.5622),
    "muc": (48.3538, 11.7861), "zrh": (47.4647, 8.5492),
    "vie": (48.1102, 16.5697), "mad": (40.4983, -3.5676),
    "bcn": (41.2974, 2.0833), "fco": (41.8003, 12.2389),
    "mxp": (45.6306, 8.7281), "arn": (59.6519, 17.9186),
    "cph": (55.6180, 12.6508), "jfk": (40.6413, -73.7781),
    "iad": (38.9531, -77.4565), "bos": (42.3656, -71.0096),
    "atl": (33.6407, -84.4277), "mia": (25.7959, -80.2870),
    "ord": (41.9742, -87.9073), "dfw": (32.8998, -97.0403),
    "den": (39.8561, -104.6737), "phx": (33.4373, -112.0078),
    "lax": (33.9416, -118.4085), "sfo": (37.6213, -122.3790),
    "sjc": (37.3639, -121.9289), "sea": (47.4502, -122.3088),
    "yyz": (43.6777, -79.6248), "yvr": (49.1967, -123.1815),
    "gru": (-23.4356, -46.4731), "syd": (-33.9399, 151.1753),
    "mel": (-37.6733, 144.8430), "akl": (-37.0082, 174.7850),
    "jnb": (-26.1392, 28.2460), "cpt": (-33.9690, 18.6020),
}
_IATA_TOKEN_RE = re.compile(r"(?:^|[^a-z])([a-z]{3})\d*(?=[^a-z]|$)", re.IGNORECASE)

_db_local = threading.local()


# ============================================================================
# 데이터 모델
# ============================================================================

@dataclass
class ProbeResult:
    rtt_ms: Optional[float] = None
    success: bool = False
    protocol: str = ""


@dataclass
class HopResult:
    ttl: int = 0
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    rtt_ms: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None
    status: str = "timeout"   # intermediate / open / closed / unreachable / timeout
    probes: List[ProbeResult] = field(default_factory=list)


@dataclass
class TracerouteResult:
    target_host: str = ""
    target_ip: str = ""
    port: int = DEFAULT_PORT
    max_hops: int = DEFAULT_MAX_HOPS
    hops: List[HopResult] = field(default_factory=list)

    def to_json(self, indent: Optional[int] = None) -> str:
        def _hop(h: HopResult):
            d = asdict(h)
            d["probes"] = [asdict(p) for p in h.probes]
            return d
        payload = {
            "target_host": self.target_host,
            "target_ip": self.target_ip,
            "port": self.port,
            "max_hops": self.max_hops,
            "hops": [_hop(h) for h in self.hops],
        }
        return json.dumps(payload, indent=indent, ensure_ascii=False)


# ============================================================================
# DNS / 지오로케이션 (기존 로직 간소화 이식)
# ============================================================================

def get_target_ip(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as e:
        raise RuntimeError(f"DNS 조회 실패: {host}: {e}")


def get_hostname(ip: str) -> Optional[str]:
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        return None


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True


def _get_cache_conn() -> Optional[sqlite3.Connection]:
    if not hasattr(_db_local, "conn"):
        try:
            os.makedirs(os.path.dirname(GEOIP_CACHE_DB), exist_ok=True)
            conn = sqlite3.connect(GEOIP_CACHE_DB, timeout=3)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS geoip (
                    ip TEXT PRIMARY KEY,
                    lat REAL, lon REAL, country TEXT, ts INTEGER
                )
            """)
            conn.commit()
            _db_local.conn = conn
        except Exception:
            _db_local.conn = None
    return _db_local.conn


def _cache_get(ip: str) -> Optional[Tuple[Optional[float], Optional[float], Optional[str]]]:
    conn = _get_cache_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT lat, lon, country, ts FROM geoip WHERE ip=?", (ip,)
        ).fetchone()
        if not row:
            return None
        lat, lon, country, ts = row
        age = time.time() - ts
        ttl = GEOIP_CACHE_NULL_TTL if (lat is None and lon is None) else GEOIP_CACHE_TTL
        if age > ttl:
            return None
        return lat, lon, country
    except Exception:
        return None


def _cache_put(ip: str, lat: Optional[float], lon: Optional[float], country: Optional[str]):
    conn = _get_cache_conn()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO geoip(ip, lat, lon, country, ts) VALUES(?,?,?,?,?)",
            (ip, lat, lon, country, int(time.time()))
        )
        conn.commit()
    except Exception:
        pass


def _hostname_iata_hint(hostname: Optional[str]) -> Optional[Tuple[float, float]]:
    if not hostname:
        return None
    for m in _IATA_TOKEN_RE.finditer(hostname.lower()):
        code = m.group(1)
        if code in IATA_HINTS:
            return IATA_HINTS[code]
    return None


def _fetch_geo_remote(ip: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        url = GEOLOCATION_API_URL.format(ip=ip)
        with urllib.request.urlopen(url, timeout=GEOLOCATION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        if not data.get("success", True):
            return None, None, None
        return data.get("latitude"), data.get("longitude"), data.get("country")
    except Exception:
        return None, None, None


def get_geolocation(ip: str, hostname: Optional[str] = None
                    ) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    if _is_private(ip):
        return None, None, None
    cached = _cache_get(ip)
    if cached is not None:
        lat, lon, country = cached
        if lat is None and (hint := _hostname_iata_hint(hostname)):
            return hint[0], hint[1], country
        return lat, lon, country
    lat, lon, country = _fetch_geo_remote(ip)
    _cache_put(ip, lat, lon, country)
    if lat is None and (hint := _hostname_iata_hint(hostname)):
        return hint[0], hint[1], country
    return lat, lon, country


# ============================================================================
# 핵심: scapy 기반 probe batch
# ============================================================================

def _classify_reply(recv, target_ip: str) -> str:
    """응답 패킷의 의미를 분류하여 status 반환."""
    if recv is None:
        return "timeout"
    # ICMP Time Exceeded → 중간 홉
    if recv.haslayer(ICMP):
        icmp_type = recv[ICMP].type
        icmp_code = recv[ICMP].code
        if icmp_type == 11:  # Time Exceeded
            return "intermediate"
        if icmp_type == 3:   # Dest Unreachable
            if icmp_code == 3:  # Port Unreachable → 최종 도달
                return "closed"
            return "unreachable"
        if icmp_type == 0:   # Echo Reply
            return "open"
    # TCP SYN+ACK → 최종 open
    if recv.haslayer(TCP):
        flags = recv[TCP].flags
        if flags & 0x12 == 0x12:  # SYN+ACK
            return "open"
        if flags & 0x14 == 0x14:  # RST+ACK
            return "closed"
    return "intermediate"


def _probe_tcp_batch(target_ip: str, ttls: List[int], port: int,
                     timeout: float, inter: float = 0.02) -> Dict[int, Tuple[str, float, str]]:
    """
    TCP SYN을 각 TTL별로 sport를 다르게 하여 배치 전송.
    반환: {ttl: (sender_ip, rtt_ms, status)}
    """
    if not ttls:
        return {}
    probes = []
    for ttl in ttls:
        sport = SPORT_BASE + ttl
        probes.append(IP(dst=target_ip, ttl=ttl) /
                      TCP(sport=sport, dport=port, flags="S", seq=ttl))
    ans, _ = sr(probes, timeout=timeout, inter=inter, verbose=0)
    result = {}
    for sent, recv in ans:
        ttl = sent[IP].ttl
        rtt_ms = (recv.time - sent.sent_time) * 1000
        result[ttl] = (recv.src, rtt_ms, _classify_reply(recv, target_ip))
    return result


def _probe_udp_batch(target_ip: str, ttls: List[int], timeout: float,
                     inter: float = 0.02) -> Dict[int, Tuple[str, float, str]]:
    if not ttls:
        return {}
    probes = []
    for ttl in ttls:
        sport = SPORT_BASE + ttl
        dport = UDP_BASE_PORT + ttl
        probes.append(IP(dst=target_ip, ttl=ttl) /
                      UDP(sport=sport, dport=dport))
    ans, _ = sr(probes, timeout=timeout, inter=inter, verbose=0)
    result = {}
    for sent, recv in ans:
        ttl = sent[IP].ttl
        rtt_ms = (recv.time - sent.sent_time) * 1000
        result[ttl] = (recv.src, rtt_ms, _classify_reply(recv, target_ip))
    return result


def _probe_icmp_batch(target_ip: str, ttls: List[int], timeout: float,
                      inter: float = 0.02) -> Dict[int, Tuple[str, float, str]]:
    if not ttls:
        return {}
    probes = []
    for ttl in ttls:
        probes.append(IP(dst=target_ip, ttl=ttl) /
                      ICMP(id=ICMP_ID_BASE + ttl, seq=ttl) /
                      Raw(load=b"trace"))
    ans, _ = sr(probes, timeout=timeout, inter=inter, verbose=0)
    result = {}
    for sent, recv in ans:
        ttl = sent[IP].ttl
        rtt_ms = (recv.time - sent.sent_time) * 1000
        result[ttl] = (recv.src, rtt_ms, _classify_reply(recv, target_ip))
    return result


# ============================================================================
# 오케스트레이션
# ============================================================================

def run_traceroute(target_host: str, *, port: int = DEFAULT_PORT,
                   max_hops: int = DEFAULT_MAX_HOPS,
                   timeout: float = DEFAULT_TIMEOUT,
                   probes: int = DEFAULT_PROBES,
                   protocol: str = PROTOCOL_ALL,
                   fallback_ports: Optional[List[int]] = None,
                   show_location: bool = True) -> TracerouteResult:
    """
    batch 방식 3-way 폴백:
    라운드 1..N(probes) 각각에서:
      1) TCP 주 포트 → 미응답 TTL 추출
      2) TCP 폴백 포트들 순차
      3) UDP
      4) ICMP
    각 TTL의 응답은 round별로 누적되고, 최상의 결과를 최종으로.
    """
    try:
        target_ip = get_target_ip(target_host)
    except RuntimeError as e:
        res = TracerouteResult(target_host=target_host, target_ip="",
                               port=port, max_hops=max_hops)
        print(f"오류: {e}", file=sys.stderr)
        return res

    fallback_ports = fallback_ports or []
    try_tcp = protocol in (PROTOCOL_TCP, PROTOCOL_BOTH, PROTOCOL_ALL)
    try_udp = protocol in (PROTOCOL_UDP, PROTOCOL_BOTH, PROTOCOL_ALL)
    try_icmp = protocol in (PROTOCOL_ICMP, PROTOCOL_ALL)

    print(f"Traceroute to {target_host} ({target_ip})", file=sys.stderr)
    proto_label = protocol.upper()
    if protocol == PROTOCOL_BOTH:
        proto_label = "TCP+UDP"
    elif protocol == PROTOCOL_ALL:
        proto_label = "TCP+UDP+ICMP"
    print(f"Protocol: {proto_label}, Port: {port}, Max hops: {max_hops}, "
          f"Probes: {probes}, Fallback: {fallback_ports}\n", file=sys.stderr)

    # 각 TTL별 probe 결과 누적
    per_ttl_probes: Dict[int, List[ProbeResult]] = {
        ttl: [] for ttl in range(1, max_hops + 1)
    }
    # 각 TTL의 최상 응답 (sender_ip, rtt, status)
    best: Dict[int, Tuple[str, float, str]] = {}

    for round_idx in range(probes):
        # 아직 응답 없는 TTL들
        missing = [t for t in range(1, max_hops + 1) if t not in best]
        if not missing:
            break

        # 1) TCP 주 포트
        if try_tcp:
            got = _probe_tcp_batch(target_ip, missing, port, timeout)
            _merge(best, got, per_ttl_probes, f"tcp:{port}")
            missing = [t for t in missing if t not in got]

        # 2) TCP 폴백 포트
        if try_tcp and missing and fallback_ports:
            for fp in fallback_ports:
                got = _probe_tcp_batch(target_ip, missing, fp, timeout)
                _merge(best, got, per_ttl_probes, f"tcp:{fp}")
                missing = [t for t in missing if t not in got]
                if not missing:
                    break

        # 3) UDP
        if try_udp and missing:
            got = _probe_udp_batch(target_ip, missing, timeout)
            _merge(best, got, per_ttl_probes, "udp")
            missing = [t for t in missing if t not in got]

        # 4) ICMP
        if try_icmp and missing:
            got = _probe_icmp_batch(target_ip, missing, timeout)
            _merge(best, got, per_ttl_probes, "icmp")
            missing = [t for t in missing if t not in got]

        # 이미 응답받은 TTL들도 추가 probe를 쏴서 RTT를 보강 (정확도 향상)
        # 단 round_idx == 0 이외에만 — 첫 라운드는 위에서 이미 처리
        # (단순화를 위해 생략 — 첫 응답이 없는 TTL만 재시도하는 것도 합리적)

        # 이번 round에서 여전히 응답 없는 TTL은 probes[] 에 실패 기록
        for t in missing:
            per_ttl_probes[t].append(ProbeResult(
                rtt_ms=None, success=False, protocol="tcp"))

    # 결과 조립
    result = TracerouteResult(target_host=target_host, target_ip=target_ip,
                              port=port, max_hops=max_hops)

    for ttl in range(1, max_hops + 1):
        if ttl in best:
            sender_ip, rtt_ms, status = best[ttl]
            hostname = get_hostname(sender_ip)
            lat, lon, country = (None, None, None)
            if show_location:
                lat, lon, country = get_geolocation(sender_ip, hostname)
            hop = HopResult(
                ttl=ttl, ip_address=sender_ip, hostname=hostname,
                rtt_ms=round(rtt_ms, 2), latitude=lat, longitude=lon,
                country=country, status=status, probes=per_ttl_probes[ttl],
            )
        else:
            hop = HopResult(ttl=ttl, status="timeout",
                            probes=per_ttl_probes[ttl])
        _print_hop(hop)
        result.hops.append(hop)
        # 최종 목적지 도달 시 이후 TTL은 잘라냄
        if hop.status in ("open", "closed", "unreachable") \
                and hop.ip_address == target_ip:
            break

    return result


def _merge(best: Dict[int, Tuple[str, float, str]],
           got: Dict[int, Tuple[str, float, str]],
           probes_log: Dict[int, List[ProbeResult]],
           label: str):
    """새로 얻은 응답들을 best에 반영 + probes 로그에 success 기록."""
    for ttl, (ip, rtt, status) in got.items():
        if ttl not in best:
            best[ttl] = (ip, rtt, status)
        probes_log[ttl].append(ProbeResult(
            rtt_ms=round(rtt, 2), success=True, protocol=label))


def _print_hop(hop: HopResult):
    rtt_parts = []
    for p in hop.probes:
        if p.success and p.rtt_ms is not None:
            rtt_parts.append(f"{p.rtt_ms:.1f}ms")
        else:
            rtt_parts.append("*")
    rtt_str = "  ".join(rtt_parts) or "*  *  *"

    if hop.status == "timeout":
        print(f"{hop.ttl}\t{rtt_str}\tRequest timed out.", file=sys.stderr)
        return

    location = ""
    if hop.latitude is not None and hop.longitude is not None:
        location = f" [{hop.latitude}, {hop.longitude}]"
    status_tag = {"open": " [Open]", "closed": " [Closed]",
                  "unreachable": " [Unreachable]"}.get(hop.status, "")

    host_str = (f"{hop.hostname} ({hop.ip_address})"
                if hop.hostname and hop.hostname != hop.ip_address
                else hop.ip_address or "*")
    print(f"{hop.ttl}\t{rtt_str}\t{host_str}{location}{status_tag}",
          file=sys.stderr)


# ============================================================================
# CLI
# ============================================================================

def parse_arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scapy 기반 다중 프로토콜 Traceroute",
    )
    p.add_argument("target", help="대상 호스트명 또는 IP")
    p.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT,
                   help=f"TCP/UDP 포트 (기본 {DEFAULT_PORT})")
    p.add_argument("-m", "--max-hops", type=int, default=DEFAULT_MAX_HOPS)
    p.add_argument("-t", "--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument("-q", "--probes", type=int, default=DEFAULT_PROBES)
    p.add_argument("-P", "--protocol",
                   choices=[PROTOCOL_TCP, PROTOCOL_UDP, PROTOCOL_ICMP,
                            PROTOCOL_BOTH, PROTOCOL_ALL],
                   default=PROTOCOL_ALL)
    p.add_argument("-F", "--fallback-ports", type=str, default="")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-location", action="store_true")
    return p.parse_args()


def main():
    args = parse_arguments()

    fallback: List[int] = []
    if args.fallback_ports:
        try:
            fallback = [int(x.strip()) for x in args.fallback_ports.split(",")
                        if x.strip()]
        except ValueError:
            print("오류: 폴백 포트는 정수", file=sys.stderr)
            sys.exit(1)

    try:
        result = run_traceroute(
            args.target, port=args.port, max_hops=args.max_hops,
            timeout=args.timeout, probes=args.probes,
            protocol=args.protocol, fallback_ports=fallback,
            show_location=not args.no_location,
        )
    except PermissionError as e:
        if args.json:
            print(json.dumps({"error": f"권한 부족: {e}",
                              "target_host": args.target, "hops": []}))
        else:
            print(f"오류: 권한 부족: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        print(result.to_json(indent=None))


if __name__ == "__main__":
    main()
