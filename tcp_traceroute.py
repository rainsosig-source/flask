#!/usr/bin/env python3
"""
TCP Traceroute - TCP 기반 네트워크 경로 추적 도구

이 도구는 TCP SYN 패킷과 TTL(Time-To-Live)을 활용하여
목적지까지의 네트워크 경로를 추적합니다.

사용법:
    python tcp_traceroute.py <target_host> [port] [options]

예시:
    python tcp_traceroute.py google.com
    python tcp_traceroute.py google.com 443
    python tcp_traceroute.py google.com --json
"""

import socket
import struct
import sys
import os
import re
import time
import select
import json
import sqlite3
import threading
import urllib.request
import argparse
import ipaddress
import errno
from typing import Optional, Tuple, Dict, List, Any
from dataclasses import dataclass, asdict
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================================
# 설정 상수
# ============================================================================

DEFAULT_PORT = 80
DEFAULT_MAX_HOPS = 30
DEFAULT_TIMEOUT = 2.0
DEFAULT_PROBES = 3  # 각 홉당 프로브 수
UDP_BASE_PORT = 33434  # UDP traceroute 시작 포트
GEOLOCATION_API_URL = "http://ip-api.com/json/{ip}"
GEOLOCATION_TIMEOUT = 1.0
GEOIP_CACHE_DB = os.environ.get(
    "TRACEROUTE_GEOIP_CACHE",
    "/opt/flask-app/cache/geoip.db",
)
GEOIP_CACHE_TTL = 30 * 86400  # 30일

# 프로토콜 모드
PROTOCOL_TCP = "tcp"
PROTOCOL_UDP = "udp"
PROTOCOL_BOTH = "both"  # TCP 실패 시 UDP 폴백


# 백본 라우터 호스트명에 자주 등장하는 IATA/도시 코드 → (위도, 경도)
# DB 미스 시 호스트명 토큰에서 위치를 보정하기 위한 힌트
IATA_HINTS: Dict[str, Tuple[float, float]] = {
    # Asia
    "icn": (37.4602, 126.4407), "gmp": (37.5583, 126.7906),
    "nrt": (35.7647, 140.3863), "hnd": (35.5494, 139.7798),
    "kix": (34.4347, 135.2440), "itm": (34.7855, 135.4382),
    "hkg": (22.3080, 113.9185), "tpe": (25.0777, 121.2328),
    "sin": (1.3644, 103.9915), "kul": (2.7456, 101.7099),
    "bkk": (13.6900, 100.7501), "cgk": (-6.1256, 106.6559),
    "del": (28.5562, 77.1000), "bom": (19.0896, 72.8656),
    "maa": (12.9941, 80.1709), "blr": (13.1986, 77.7066),
    # Middle East
    "dxb": (25.2532, 55.3657), "auh": (24.4330, 54.6511),
    "doh": (25.2731, 51.6080),
    # Europe
    "lhr": (51.4700, -0.4543), "lcy": (51.5048, 0.0495),
    "ams": (52.3105, 4.7683), "cdg": (49.0097, 2.5479),
    "fra": (50.0379, 8.5622), "muc": (48.3538, 11.7861),
    "zrh": (47.4647, 8.5492), "vie": (48.1102, 16.5697),
    "mad": (40.4983, -3.5676), "bcn": (41.2974, 2.0833),
    "fco": (41.8003, 12.2389), "mxp": (45.6306, 8.7281),
    "arn": (59.6519, 17.9186), "cph": (55.6180, 12.6508),
    "hel": (60.3172, 24.9633), "osl": (60.1976, 11.1004),
    "dub": (53.4264, -6.2499), "waw": (52.1657, 20.9671),
    "prg": (50.1008, 14.2632), "buh": (44.5711, 26.0850),
    "ist": (41.2753, 28.7519), "svo": (55.9726, 37.4146),
    # North America
    "jfk": (40.6413, -73.7781), "lga": (40.7769, -73.8740),
    "ewr": (40.6895, -74.1745),
    "iad": (38.9531, -77.4565), "dca": (38.8512, -77.0402),
    "bos": (42.3656, -71.0096), "phl": (39.8744, -75.2424),
    "atl": (33.6407, -84.4277), "mia": (25.7959, -80.2870),
    "ord": (41.9742, -87.9073), "mdw": (41.7868, -87.7522),
    "dfw": (32.8998, -97.0403), "iah": (29.9902, -95.3368),
    "den": (39.8561, -104.6737), "phx": (33.4373, -112.0078),
    "lax": (33.9416, -118.4085), "sfo": (37.6213, -122.3790),
    "sjc": (37.3639, -121.9289), "sea": (47.4502, -122.3088),
    "yyz": (43.6777, -79.6248), "yul": (45.4706, -73.7408),
    "yvr": (49.1967, -123.1815),
    # South America
    "gru": (-23.4356, -46.4731), "gig": (-22.8099, -43.2505),
    "scl": (-33.3930, -70.7858), "bog": (4.7016, -74.1469),
    "eze": (-34.8222, -58.5358),
    # Africa
    "jnb": (-26.1392, 28.2460), "cpt": (-33.9690, 18.6020),
    "cai": (30.1219, 31.4056), "los": (6.5774, 3.3211),
    # Oceania
    "syd": (-33.9399, 151.1753), "mel": (-37.6733, 144.8430),
    "akl": (-37.0082, 174.7850),
}

_IATA_TOKEN_RE = re.compile(r"(?:^|[^a-z])([a-z]{3})\d*(?=[^a-z]|$)", re.IGNORECASE)

# SQLite 연결을 스레드별로 분리 (sqlite3는 같은 conn을 다중 스레드에서 쓰면 위험)
_db_local = threading.local()


# ============================================================================
# 데이터 클래스
# ============================================================================

@dataclass
class ProbeResult:
    """단일 프로브 결과"""
    rtt_ms: Optional[float] = None
    success: bool = False
    protocol: str = "tcp"  # tcp 또는 udp


@dataclass
class HopResult:
    """단일 홉의 결과를 저장하는 데이터 클래스"""
    ttl: int
    ip_address: Optional[str] = None
    hostname: Optional[str] = None
    rtt_ms: Optional[float] = None  # 첫 번째 성공한 프로브의 RTT
    probes: List[ProbeResult] = None  # 모든 프로브 결과
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    country: Optional[str] = None  # 국가 정보
    status: str = "timeout"  # timeout, open, closed, intermediate
    
    def __post_init__(self):
        if self.probes is None:
            self.probes = []


@dataclass
class TracerouteResult:
    """전체 Traceroute 결과를 저장하는 데이터 클래스"""
    target_host: str
    target_ip: str
    port: int
    max_hops: int
    hops: List[HopResult] = None
    
    def __post_init__(self):
        if self.hops is None:
            self.hops = []
    
    def to_dict(self) -> Dict[str, Any]:
        """결과를 딕셔너리로 변환"""
        return {
            "target_host": self.target_host,
            "target_ip": self.target_ip,
            "port": self.port,
            "max_hops": self.max_hops,
            "hops": [asdict(hop) for hop in self.hops]
        }
    
    def to_json(self, indent: int = 2) -> str:
        """결과를 JSON 문자열로 변환"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ============================================================================
# 유틸리티 함수
# ============================================================================

def is_private_ip(ip: str) -> bool:
    """
    IP 주소가 사설(Private) IP인지 확인합니다.
    
    Args:
        ip: 확인할 IP 주소 문자열
        
    Returns:
        사설 IP이면 True, 아니면 False
    """
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved
    except ValueError:
        return False


class TracerouteError(RuntimeError):
    """Traceroute 실행 중 발생한 회복 불가 오류."""


def get_target_ip(host: str) -> str:
    """호스트명을 IP 주소로 변환. 실패 시 TracerouteError."""
    try:
        return socket.gethostbyname(host)
    except socket.gaierror as e:
        raise TracerouteError(f"호스트 '{host}'를 찾을 수 없습니다. ({e})") from e


def get_local_ip(target_ip: str) -> str:
    """
    대상 IP에 연결 시 사용할 로컬 IP 주소를 반환합니다.
    
    Args:
        target_ip: 대상 IP 주소
        
    Returns:
        로컬 네트워크 인터페이스의 IP 주소
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_ip, 1))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


@lru_cache(maxsize=256)
def get_hostname(ip: str) -> str:
    """
    IP 주소의 호스트명을 조회합니다. (캐싱 적용)
    
    Args:
        ip: 조회할 IP 주소
        
    Returns:
        호스트명 또는 IP 주소 (조회 실패 시)
    """
    try:
        return socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror):
        return ip


def _get_cache_conn() -> Optional[sqlite3.Connection]:
    """스레드 로컬 SQLite 연결을 반환. 디렉터리/테이블 없으면 생성."""
    conn = getattr(_db_local, "conn", None)
    if conn is not None:
        return conn
    try:
        os.makedirs(os.path.dirname(GEOIP_CACHE_DB), exist_ok=True)
        conn = sqlite3.connect(GEOIP_CACHE_DB, timeout=2.0, isolation_level=None)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS geoip (
                ip         TEXT PRIMARY KEY,
                lat        REAL,
                lon        REAL,
                country    TEXT,
                cached_at  INTEGER NOT NULL
            )
        """)
        conn.execute("PRAGMA journal_mode=WAL")
        _db_local.conn = conn
        return conn
    except (OSError, sqlite3.Error):
        # 캐시는 옵셔널. 실패해도 계속 동작.
        _db_local.conn = None
        return None


def _cache_get(ip: str) -> Optional[Tuple[Optional[float], Optional[float], Optional[str]]]:
    conn = _get_cache_conn()
    if conn is None:
        return None
    try:
        cutoff = int(time.time()) - GEOIP_CACHE_TTL
        row = conn.execute(
            "SELECT lat, lon, country FROM geoip WHERE ip=? AND cached_at>?",
            (ip, cutoff),
        ).fetchone()
        return (row[0], row[1], row[2]) if row else None
    except sqlite3.Error:
        return None


def _cache_put(ip: str, lat: Optional[float], lon: Optional[float], country: Optional[str]) -> None:
    conn = _get_cache_conn()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO geoip(ip, lat, lon, country, cached_at) VALUES (?,?,?,?,?)",
            (ip, lat, lon, country, int(time.time())),
        )
    except sqlite3.Error:
        pass


def hostname_iata_hint(hostname: Optional[str]) -> Optional[Tuple[float, float]]:
    """호스트명에서 IATA 3글자 코드를 찾아 (위도, 경도)를 반환. 없으면 None."""
    if not hostname:
        return None
    for match in _IATA_TOKEN_RE.finditer(hostname):
        code = match.group(1).lower()
        if code in IATA_HINTS:
            return IATA_HINTS[code]
    return None


def _fetch_geolocation_remote(ip: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        url = GEOLOCATION_API_URL.format(ip=ip)
        request = urllib.request.Request(
            url,
            headers={'User-Agent': 'TCP-Traceroute/1.0'}
        )
        with urllib.request.urlopen(request, timeout=GEOLOCATION_TIMEOUT) as response:
            data = json.loads(response.read().decode())
            if data.get('status') == 'success':
                return data.get('lat'), data.get('lon'), data.get('country')
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        pass
    return None, None, None


@lru_cache(maxsize=512)
def get_geolocation(
    ip: str,
    hostname: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """IP 위경도/국가 조회. SQLite 영구 캐시 + IATA 호스트명 힌트 + 원격 호출 순."""
    if is_private_ip(ip):
        return None, None, None

    cached = _cache_get(ip)
    if cached is not None:
        lat, lon, country = cached
        # 캐시에 위경도가 비어있고 호스트명 힌트가 있으면 보정해서 사용
        if (lat is None or lon is None) and hostname:
            hint = hostname_iata_hint(hostname)
            if hint is not None:
                return hint[0], hint[1], country
        return lat, lon, country

    lat, lon, country = _fetch_geolocation_remote(ip)
    # 원격이 위경도 비웠으면 호스트명에서 IATA 힌트로 보강
    if (lat is None or lon is None) and hostname:
        hint = hostname_iata_hint(hostname)
        if hint is not None:
            lat, lon = hint[0], hint[1]
    _cache_put(ip, lat, lon, country)
    return lat, lon, country


def get_connection_refused_errno() -> int:
    """
    현재 플랫폼의 Connection Refused 에러 코드를 반환합니다.
    
    Returns:
        에러 코드 정수
    """
    if sys.platform == 'win32':
        return 10061  # WSAECONNREFUSED
    return errno.ECONNREFUSED


# ============================================================================
# 소켓 관리 클래스
# ============================================================================

class ReceiverSocket:
    """ICMP 응답을 수신하기 위한 Raw 소켓 관리 클래스"""
    
    def __init__(self, bind_ip: str):
        """
        수신 소켓을 초기화합니다.
        
        Args:
            bind_ip: 바인딩할 로컬 IP 주소
            
        Raises:
            SystemExit: 권한 부족 또는 소켓 생성 실패 시
        """
        self.bind_ip = bind_ip
        self.socket = None
        self.is_windows = sys.platform == 'win32'
        self._create_socket()
    
    def _create_socket(self):
        """플랫폼에 맞는 Raw 소켓을 생성합니다."""
        try:
            if self.is_windows:
                self.socket = socket.socket(
                    socket.AF_INET, 
                    socket.SOCK_RAW, 
                    socket.IPPROTO_IP
                )
                self.socket.bind((self.bind_ip, 0))
                self.socket.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
            else:
                self.socket = socket.socket(
                    socket.AF_INET, 
                    socket.SOCK_RAW, 
                    socket.IPPROTO_ICMP
                )
                try:
                    self.socket.bind((self.bind_ip, 0))
                except OSError:
                    pass  # 일부 Linux 환경에서는 바인딩이 불필요할 수 있음
            
            self.socket.setblocking(False)
            
        except PermissionError as e:
            raise TracerouteError(self._permission_error_msg()) from e
        except OSError as e:
            raise TracerouteError(f"수신 소켓 생성 실패 - {e}") from e

    def _permission_error_msg(self) -> str:
        if self.is_windows:
            return ("Raw 소켓 생성에 관리자 권한이 필요합니다. "
                    "PowerShell을 관리자 권한으로 실행하세요.")
        return "Raw 소켓 생성에 관리자 권한이 필요합니다. sudo로 실행하세요."
    
    def receive(self, buffer_size: int = 512) -> Tuple[bytes, Tuple[str, int]]:
        """
        패킷을 수신합니다.
        
        Args:
            buffer_size: 수신 버퍼 크기
            
        Returns:
            (패킷 데이터, (송신자 IP, 포트)) 튜플
        """
        return self.socket.recvfrom(buffer_size)
    
    def fileno(self) -> int:
        """소켓의 파일 디스크립터를 반환합니다."""
        return self.socket.fileno()
    
    def close(self):
        """소켓을 정리하고 닫습니다."""
        if self.socket:
            try:
                if self.is_windows:
                    self.socket.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
            except OSError:
                pass
            finally:
                self.socket.close()
                self.socket = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


# ============================================================================
# 패킷 파서
# ============================================================================

class PacketParser:
    """네트워크 패킷을 파싱하는 유틸리티 클래스"""
    
    ICMP_TIME_EXCEEDED = 11
    ICMP_DEST_UNREACHABLE = 3
    PROTOCOL_ICMP = 1
    
    @staticmethod
    def parse_ip_header(packet: bytes) -> Tuple[int, int, int]:
        """
        IP 헤더를 파싱합니다.
        
        Args:
            packet: 원시 패킷 데이터
            
        Returns:
            (프로토콜, IP 헤더 길이, 전체 길이) 튜플
        """
        if len(packet) < 20:
            raise ValueError("패킷이 너무 짧습니다")
        
        ip_header = struct.unpack('!BBHHHBBH4s4s', packet[:20])
        version_ihl = ip_header[0]
        ihl = version_ihl & 0x0F
        ip_header_len = ihl * 4
        protocol = ip_header[6]
        total_length = ip_header[2]
        
        return protocol, ip_header_len, total_length
    
    @staticmethod
    def parse_icmp_header(packet: bytes) -> Tuple[int, int]:
        """
        ICMP 헤더를 파싱합니다.
        
        Args:
            packet: ICMP 패킷 데이터
            
        Returns:
            (ICMP 타입, ICMP 코드) 튜플
        """
        if len(packet) < 8:
            raise ValueError("ICMP 패킷이 너무 짧습니다")
        
        return struct.unpack('!BB', packet[:2])
    
    @staticmethod
    def parse_tcp_ports(packet: bytes) -> Tuple[int, int]:
        """
        TCP 헤더에서 포트 정보를 파싱합니다.
        
        Args:
            packet: TCP 패킷 데이터
            
        Returns:
            (소스 포트, 목적지 포트) 튜플
        """
        if len(packet) < 4:
            raise ValueError("TCP 패킷이 너무 짧습니다")
        
        return struct.unpack('!HH', packet[:4])
    
    @classmethod
    def parse_icmp_response(cls, packet: bytes, expected_src_port: int, 
                           expected_dst_port: int) -> Optional[Tuple[int, str]]:
        """
        ICMP 응답 패킷을 파싱하여 관련 정보를 추출합니다.
        
        Args:
            packet: 원시 패킷 데이터
            expected_src_port: 예상 소스 포트
            expected_dst_port: 예상 목적지 포트
            
        Returns:
            (ICMP 타입, 송신자 IP) 튜플 또는 None
        """
        try:
            protocol, ip_header_len, _ = cls.parse_ip_header(packet)
            
            if protocol != cls.PROTOCOL_ICMP:
                return None
            
            icmp_packet = packet[ip_header_len:]
            icmp_type, icmp_code = cls.parse_icmp_header(icmp_packet)
            
            if icmp_type not in (cls.ICMP_TIME_EXCEEDED, cls.ICMP_DEST_UNREACHABLE):
                return None
            
            # ICMP 페이로드에서 원본 IP/TCP 헤더 추출
            icmp_payload = icmp_packet[8:]
            if len(icmp_payload) < 20:
                return None
            
            _, inner_ip_header_len, _ = cls.parse_ip_header(icmp_payload)
            inner_tcp = icmp_payload[inner_ip_header_len:inner_ip_header_len + 8]
            
            if len(inner_tcp) < 4:
                return None
            
            src_port, dst_port = cls.parse_tcp_ports(inner_tcp)
            
            if src_port == expected_src_port and dst_port == expected_dst_port:
                return icmp_type, None  # 송신자 IP는 recvfrom에서 얻음
            
        except (ValueError, struct.error):
            pass
        
        return None


# ============================================================================
# 메인 Traceroute 클래스
# ============================================================================

class Traceroute:
    """TCP/UDP 기반 Traceroute 수행 클래스"""
    
    def __init__(self, target_host: str, port: int = DEFAULT_PORT,
                 max_hops: int = DEFAULT_MAX_HOPS, timeout: float = DEFAULT_TIMEOUT,
                 probes: int = DEFAULT_PROBES, protocol: str = PROTOCOL_TCP,
                 fallback_ports: List[int] = None,
                 verbose: bool = False, show_location: bool = True):
        """
        Traceroute 인스턴스를 초기화합니다.
        
        Args:
            target_host: 대상 호스트명 또는 IP
            port: TCP/UDP 포트 번호
            max_hops: 최대 홉 수
            timeout: 각 홉의 타임아웃 (초)
            probes: 각 홉당 프로브 횟수
            protocol: 프로토콜 모드 (tcp, udp, both)
            fallback_ports: TCP 실패 시 시도할 추가 포트 목록
            verbose: 상세 출력 여부
            show_location: 위치 정보 표시 여부
        """
        self.target_host = target_host
        self.target_ip = get_target_ip(target_host)
        self.port = port
        self.max_hops = max_hops
        self.timeout = timeout
        self.probes = probes
        self.protocol = protocol
        self.fallback_ports = fallback_ports or []
        self.verbose = verbose
        self.show_location = show_location
        self.connection_refused_errno = get_connection_refused_errno()
        
        self.local_ip = get_local_ip(self.target_ip)
        self.udp_port_counter = UDP_BASE_PORT  # UDP 포트 카운터
        self.result = TracerouteResult(
            target_host=target_host,
            target_ip=self.target_ip,
            port=port,
            max_hops=max_hops
        )
    
    def _log(self, message: str):
        """상세 모드에서 메시지를 출력합니다."""
        if self.verbose:
            print(f"[DEBUG] {message}", file=sys.stderr)
    
    def _create_sender_socket(self, ttl: int) -> Optional[socket.socket]:
        """
        지정된 TTL로 송신 소켓을 생성합니다.
        
        Args:
            ttl: Time-To-Live 값
            
        Returns:
            설정된 소켓 또는 None (실패 시)
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            sock.setblocking(False)
            sock.bind(("", 0))
            return sock
        except OSError as e:
            self._log(f"송신 소켓 생성 실패: {e}")
            return None
    
    def _check_connection_result(self, sender_socket: socket.socket, 
                                  start_time: float) -> Optional[HopResult]:
        """
        소켓 연결 결과를 확인합니다.
        
        Args:
            sender_socket: 송신 소켓
            start_time: 연결 시작 시간
            
        Returns:
            HopResult 또는 None
        """
        try:
            so_error = sender_socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            rtt = (time.time() - start_time) * 1000
            
            if so_error == 0:
                hostname = get_hostname(self.target_ip)
                lat, lon, country = (None, None, None)
                if self.show_location:
                    lat, lon, country = get_geolocation(self.target_ip, hostname)
                return HopResult(
                    ttl=0,
                    ip_address=self.target_ip,
                    hostname=hostname,
                    rtt_ms=round(rtt, 2),
                    latitude=lat,
                    longitude=lon,
                    country=country,
                    status="open"
                )
            elif so_error == self.connection_refused_errno:
                hostname = get_hostname(self.target_ip)
                lat, lon, country = (None, None, None)
                if self.show_location:
                    lat, lon, country = get_geolocation(self.target_ip, hostname)
                return HopResult(
                    ttl=0,
                    ip_address=self.target_ip,
                    hostname=hostname,
                    rtt_ms=round(rtt, 2),
                    latitude=lat,
                    longitude=lon,
                    country=country,
                    status="closed"
                )
        except OSError:
            pass
        
        return None
    
    def _process_icmp_response(self, recv_socket: ReceiverSocket,
                                local_port: int, start_time: float) -> Optional[HopResult]:
        """
        ICMP 응답을 처리합니다.
        
        Args:
            recv_socket: 수신 소켓
            local_port: 로컬 포트 (패킷 식별용)
            start_time: 연결 시작 시간
            
        Returns:
            HopResult 또는 None
        """
        try:
            packet, addr = recv_socket.receive()
            recv_time = time.time()
            
            result = PacketParser.parse_icmp_response(
                packet, local_port, self.port
            )
            
            if result is None:
                return None
            
            icmp_type, _ = result
            rtt = (recv_time - start_time) * 1000
            sender_ip = addr[0]

            hostname = get_hostname(sender_ip)
            lat, lon, country = (None, None, None)
            if self.show_location:
                lat, lon, country = get_geolocation(sender_ip, hostname)

            status = "intermediate"
            if icmp_type == PacketParser.ICMP_DEST_UNREACHABLE:
                status = "unreachable"

            return HopResult(
                ttl=0,
                ip_address=sender_ip,
                hostname=hostname,
                rtt_ms=round(rtt, 2),
                latitude=lat,
                longitude=lon,
                country=country,
                status=status
            )
            
        except (BlockingIOError, OSError):
            return None
    
    def _single_tcp_probe(self, ttl: int, recv_socket: ReceiverSocket, 
                          target_port: int = None) -> Optional[HopResult]:
        """
        TCP 단일 프로브를 수행합니다.
        
        Args:
            ttl: Time-To-Live 값
            recv_socket: ICMP 수신 소켓
            target_port: 대상 포트 (None이면 self.port 사용)
            
        Returns:
            HopResult 또는 None (타임아웃 시)
        """
        port = target_port or self.port
        sender_socket = self._create_sender_socket(ttl)
        if sender_socket is None:
            return None
        
        try:
            local_port = sender_socket.getsockname()[1]
            start_time = time.time()
            
            # SYN 패킷 전송 (비동기)
            try:
                sender_socket.connect_ex((self.target_ip, port))
            except (BlockingIOError, OSError):
                pass
            
            # 응답 대기
            while True:
                time_left = start_time + self.timeout - time.time()
                if time_left <= 0:
                    break
                
                ready = select.select(
                    [recv_socket.socket], 
                    [sender_socket], 
                    [], 
                    min(time_left, 0.1)
                )
                
                # 연결 완료 확인 (최종 홉)
                if sender_socket in ready[1]:
                    hop_result = self._check_connection_result(
                        sender_socket, start_time
                    )
                    if hop_result:
                        hop_result.ttl = ttl
                        return hop_result
                
                # ICMP 응답 확인 (중간 홉)
                if recv_socket.socket in ready[0]:
                    hop_result = self._process_icmp_response(
                        recv_socket, local_port, start_time
                    )
                    if hop_result:
                        hop_result.ttl = ttl
                        return hop_result
            
        finally:
            sender_socket.close()
        
        return None
    
    def _single_udp_probe(self, ttl: int, recv_socket: ReceiverSocket) -> Optional[HopResult]:
        """
        UDP 단일 프로브를 수행합니다.
        
        Args:
            ttl: Time-To-Live 값
            recv_socket: ICMP 수신 소켓
            
        Returns:
            HopResult 또는 None (타임아웃 시)
        """
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            udp_socket.setblocking(False)
            
            # 포트 할당
            target_port = self.udp_port_counter
            self.udp_port_counter += 1
            if self.udp_port_counter > 33534:  # 포트 범위 제한
                self.udp_port_counter = UDP_BASE_PORT
            
            start_time = time.time()
            
            # UDP 패킷 전송
            try:
                udp_socket.sendto(b'', (self.target_ip, target_port))
            except OSError:
                pass
            
            local_port = udp_socket.getsockname()[1]
            
            # ICMP 응답 대기
            while True:
                time_left = start_time + self.timeout - time.time()
                if time_left <= 0:
                    break
                
                ready = select.select([recv_socket.socket], [], [], min(time_left, 0.1))
                
                if recv_socket.socket in ready[0]:
                    try:
                        packet, addr = recv_socket.receive()
                        recv_time = time.time()
                        
                        # IP 헤더 파싱
                        protocol, ip_header_len, _ = PacketParser.parse_ip_header(packet)
                        
                        if protocol == PacketParser.PROTOCOL_ICMP:
                            icmp_packet = packet[ip_header_len:]
                            icmp_type, icmp_code = PacketParser.parse_icmp_header(icmp_packet)
                            
                            # TTL 만료 또는 목적지 도달 불가
                            if icmp_type in (PacketParser.ICMP_TIME_EXCEEDED, 
                                           PacketParser.ICMP_DEST_UNREACHABLE):
                                rtt = (recv_time - start_time) * 1000
                                sender_ip = addr[0]

                                hostname = get_hostname(sender_ip)
                                lat, lon, country = (None, None, None)
                                if self.show_location:
                                    lat, lon, country = get_geolocation(sender_ip, hostname)

                                status = "intermediate"
                                if icmp_type == 3 and icmp_code == 3:
                                    status = "closed"
                                elif icmp_type == 3:
                                    status = "unreachable"

                                return HopResult(
                                    ttl=ttl,
                                    ip_address=sender_ip,
                                    hostname=hostname,
                                    rtt_ms=round(rtt, 2),
                                    latitude=lat,
                                    longitude=lon,
                                    country=country,
                                    status=status
                                )
                    except (ValueError, struct.error, BlockingIOError):
                        continue
            
        except OSError as e:
            self._log(f"UDP 소켓 오류: {e}")
        finally:
            try:
                udp_socket.close()
            except:
                pass
        
        return None
    
    def _probe_hop_with_retries(self, ttl: int, recv_socket: ReceiverSocket) -> HopResult:
        """
        특정 TTL로 홉을 다중 프로빙합니다.
        프로토콜 모드와 폴백 포트를 지원합니다.
        
        Args:
            ttl: Time-To-Live 값
            recv_socket: ICMP 수신 소켓
            
        Returns:
            HopResult (프로브 결과 포함)
        """
        probe_results = []
        first_successful_result = None
        
        for probe_num in range(self.probes):
            result = None
            protocol_used = "tcp"
            
            # 1. TCP 프로빙 (tcp 또는 both 모드)
            if self.protocol in (PROTOCOL_TCP, PROTOCOL_BOTH):
                result = self._single_tcp_probe(ttl, recv_socket)
                protocol_used = "tcp"
            
            # 2. TCP 실패 시 폴백 포트 시도
            if result is None and self.fallback_ports:
                for fallback_port in self.fallback_ports:
                    result = self._single_tcp_probe(ttl, recv_socket, fallback_port)
                    if result is not None:
                        protocol_used = f"tcp:{fallback_port}"
                        break
            
            # 3. UDP 프로빙 (udp 모드 또는 both 모드에서 TCP 실패 시)
            if result is None and self.protocol in (PROTOCOL_UDP, PROTOCOL_BOTH):
                result = self._single_udp_probe(ttl, recv_socket)
                protocol_used = "udp"
            
            if result is not None:
                probe_results.append(ProbeResult(
                    rtt_ms=result.rtt_ms, 
                    success=True, 
                    protocol=protocol_used
                ))
                if first_successful_result is None:
                    first_successful_result = result
            else:
                probe_results.append(ProbeResult(rtt_ms=None, success=False, protocol=protocol_used))
        
        if first_successful_result is not None:
            first_successful_result.probes = probe_results
            return first_successful_result
        else:
            return HopResult(ttl=ttl, status="timeout", probes=probe_results)
    
    def _probe_hop_owns_socket(self, ttl: int) -> HopResult:
        """병렬 실행을 위해 자체 ReceiverSocket을 생성하고 한 홉을 처리."""
        try:
            with ReceiverSocket(self.local_ip) as recv_socket:
                return self._probe_hop_with_retries(ttl, recv_socket)
        except TracerouteError:
            # 권한 등 회복 불가 — 상위에서 처리하도록 다시 raise
            raise
        except Exception as e:
            self._log(f"hop {ttl} 처리 실패: {e}")
            return HopResult(ttl=ttl, status="timeout", probes=[])

    def run(self) -> TracerouteResult:
        """Traceroute 실행. 모든 홉을 병렬로 프로빙하고 TTL 순으로 정렬."""
        protocol_info = self.protocol.upper()
        if self.protocol == PROTOCOL_BOTH:
            protocol_info = "TCP+UDP"

        fallback_info = ""
        if self.fallback_ports:
            fallback_info = f", Fallback ports: {self.fallback_ports}"

        print(f"Traceroute to {self.target_host} ({self.target_ip})", file=sys.stderr)
        print(
            f"Protocol: {protocol_info}, Port: {self.port}, "
            f"Max hops: {self.max_hops}, Probes: {self.probes}{fallback_info}\n",
            file=sys.stderr,
        )
        self._log(f"로컬 IP: {self.local_ip}")

        # 권한 사전 검증 (병렬 워커 띄우기 전 빠른 실패)
        try:
            with ReceiverSocket(self.local_ip):
                pass
        except TracerouteError:
            raise

        # max_hops 만큼 병렬 프로빙. 워커는 홉 수 + 약간의 헤드룸으로 제한.
        max_workers = min(self.max_hops, 16)
        hops_by_ttl: Dict[int, HopResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hop") as pool:
            futures = {pool.submit(self._probe_hop_owns_socket, ttl): ttl
                       for ttl in range(1, self.max_hops + 1)}
            for fut in as_completed(futures):
                ttl = futures[fut]
                try:
                    hops_by_ttl[ttl] = fut.result()
                except TracerouteError:
                    # 한 워커가 회복 불가 오류면 나머지는 결과 없이 종료
                    raise

        # TTL 순서로 정렬하면서 목적지 도달 후 추가 홉은 잘라냄
        truncate_at: Optional[int] = None
        for ttl in range(1, self.max_hops + 1):
            hop = hops_by_ttl.get(ttl, HopResult(ttl=ttl, status="timeout", probes=[]))
            self._print_hop(hop)
            self.result.hops.append(hop)
            if (hop.status in ("open", "closed", "unreachable")
                    and hop.ip_address == self.target_ip):
                truncate_at = ttl
                break

        if truncate_at is not None:
            self.result.hops = self.result.hops[:truncate_at]

        return self.result
    
    def _print_hop(self, hop: HopResult):
        """
        홉 결과를 출력합니다.
        
        Args:
            hop: 출력할 HopResult
        """
        # 프로브 RTT 문자열 생성
        if hop.probes:
            rtt_parts = []
            for probe in hop.probes:
                if probe.success and probe.rtt_ms is not None:
                    rtt_parts.append(f"{probe.rtt_ms:.1f}ms")
                else:
                    rtt_parts.append("*")
            rtt_str = "  ".join(rtt_parts)
        else:
            rtt_str = "*  *  *"
        
        # 모든 프로브가 타임아웃인 경우
        if hop.status == "timeout":
            print(f"{hop.ttl}\t{rtt_str}\tRequest timed out.", file=sys.stderr)
            return
        
        # 위치 정보 문자열
        location_str = ""
        if hop.latitude is not None and hop.longitude is not None:
            location_str = f" [{hop.latitude}, {hop.longitude}]"
        
        # 상태 표시
        status_str = ""
        if hop.status == "open":
            status_str = " [Open]"
        elif hop.status == "closed":
            status_str = " [Closed]"
        elif hop.status == "unreachable":
            status_str = " [Unreachable]"
        
        # 호스트명과 IP
        if hop.hostname and hop.hostname != hop.ip_address:
            host_str = f"{hop.hostname} ({hop.ip_address})"
        else:
            host_str = hop.ip_address
        
        print(f"{hop.ttl}\t{rtt_str}\t{host_str}{location_str}{status_str}", file=sys.stderr)


# ============================================================================
# CLI 인터페이스
# ============================================================================

def parse_arguments() -> argparse.Namespace:
    """명령줄 인수를 파싱합니다."""
    parser = argparse.ArgumentParser(
        description="TCP/UDP 기반 네트워크 경로 추적 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python tcp_traceroute.py google.com
  python tcp_traceroute.py google.com 443
  python tcp_traceroute.py google.com --protocol both
  python tcp_traceroute.py google.com --fallback-ports 443,8080
  python tcp_traceroute.py google.com --json --no-location
        """
    )
    
    parser.add_argument(
        "target",
        help="대상 호스트명 또는 IP 주소"
    )
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=DEFAULT_PORT,
        help=f"대상 TCP/UDP 포트 (기본값: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "-m", "--max-hops",
        type=int,
        default=DEFAULT_MAX_HOPS,
        help=f"최대 홉 수 (기본값: {DEFAULT_MAX_HOPS})"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"각 홉의 타임아웃 초 (기본값: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument(
        "-q", "--probes",
        type=int,
        default=DEFAULT_PROBES,
        help=f"각 홉당 프로브 횟수 (기본값: {DEFAULT_PROBES})"
    )
    parser.add_argument(
        "-P", "--protocol",
        choices=["tcp", "udp", "both"],
        default=PROTOCOL_TCP,
        help="프로토콜 모드: tcp, udp, both (기본값: tcp)"
    )
    parser.add_argument(
        "-F", "--fallback-ports",
        type=str,
        default="",
        help="TCP 실패 시 시도할 폴백 포트 (콤마 구분, 예: 443,8080)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="상세 디버그 정보 출력"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON 형식으로 출력"
    )
    parser.add_argument(
        "--no-location",
        action="store_true",
        help="지리적 위치 정보 비활성화"
    )
    
    return parser.parse_args()


def main():
    """메인 진입점. --json 모드에서 stdout은 JSON만, 사람용 표는 stderr."""
    args = parse_arguments()

    fallback_ports: List[int] = []
    if args.fallback_ports:
        try:
            fallback_ports = [int(p.strip()) for p in args.fallback_ports.split(",")]
        except ValueError:
            print("오류: 폴백 포트는 정수여야 합니다 (예: 443,8080)", file=sys.stderr)
            sys.exit(1)

    try:
        traceroute = Traceroute(
            target_host=args.target,
            port=args.port,
            max_hops=args.max_hops,
            timeout=args.timeout,
            probes=args.probes,
            protocol=args.protocol,
            fallback_ports=fallback_ports,
            verbose=args.verbose,
            show_location=not args.no_location,
        )
        result = traceroute.run()
    except TracerouteError as e:
        if args.json:
            error_doc = {"error": str(e), "target_host": args.target, "hops": []}
            print(json.dumps(error_doc, ensure_ascii=False))
        else:
            print(f"오류: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        # stdout은 JSON 한 줄만 — 호출자(blueprint)가 안전하게 json.loads 가능
        print(result.to_json(indent=None))


if __name__ == "__main__":
    main()
