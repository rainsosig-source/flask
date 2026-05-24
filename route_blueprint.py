"""
Traceroute 웹 인터페이스 - Flask Blueprint
tcp_traceroute.py를 사용하여 경로 추적 + 3D 지구본 시각화
"""

import os
import re
import json
import socket
import ipaddress
import subprocess
from threading import Semaphore

from flask import Blueprint, render_template, request, jsonify, current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

route_bp = Blueprint('route', __name__)

# IP당 rate-limit. 익명 정찰 도구화 방지.
# 메모리 스토리지는 gunicorn 워커별로 분리되므로 실제 IP당 한도는 (한도 × 워커수).
# 두 단계 방어로 충분: (1) 세마포어가 동시 2건으로 잡고 (2) 분당 한도가 burst 차단.
limiter = Limiter(key_func=get_remote_address, default_limits=[])

# 호스트명/IP 화이트리스트 — sudo 인자 인젝션 방지의 핵심 방어선
_HOSTNAME_RE = re.compile(r'^[a-zA-Z0-9.\-]{1,253}$')

# 동시 traceroute 동시성 제한 — 워커 잠금/네트워크 abuse 방지
_TRACE_CONCURRENCY = int(os.environ.get('ROUTE_MAX_CONCURRENT', '2'))
_trace_semaphore = Semaphore(_TRACE_CONCURRENCY)

# 백엔드 타임아웃은 프론트와 일치(60s)시켜 워커가 응답 후 매달리지 않도록.
_TRACE_TIMEOUT_SEC = int(os.environ.get('ROUTE_TRACE_TIMEOUT', '60'))

_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tcp_traceroute.py')

# TCP 주 포트가 막힐 때 시도할 폴백 포트. 인터넷 경로에서 널리 허용되는 포트들.
# 80(HTTP), 53(DNS), 22(SSH).
_FALLBACK_PORTS = os.environ.get("ROUTE_FALLBACK_PORTS", "80,53,22")


def _validate_target(target: str) -> bool:
    if not _HOSTNAME_RE.match(target or ''):
        return False
    # SSRF/내부망 정찰 방지: 해석 IP가 사설/로컬/링크로컬/예약이면 거부
    try:
        for *_, sockaddr in socket.getaddrinfo(target, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
    except Exception:
        return False
    return True


def run_tcp_traceroute(target: str, max_hops: int, probes: int, protocol: str) -> dict:
    """tcp_traceroute.py 서브프로세스를 실행하여 경로를 추적."""
    if not _validate_target(target):
        return {'success': False, 'error': '잘못된 호스트명입니다.', 'hops': []}

    cmd = [
        'sudo', '-n', 'python3', _SCRIPT_PATH,
        target,
        '-m', str(max_hops),
        '-q', str(probes),
        '-P', protocol,
        '-F', _FALLBACK_PORTS,
        '--json',
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TRACE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': f'시간 초과 ({_TRACE_TIMEOUT_SEC}s)', 'hops': []}
    except FileNotFoundError:
        return {'success': False, 'error': '실행 파일을 찾을 수 없습니다.', 'hops': []}

    if not result.stdout.strip():
        # stderr는 사람용 표/디버그. 로깅만 하고 사용자 메시지는 일반화.
        current_app.logger.warning('traceroute empty stdout: rc=%s stderr=%r',
                                   result.returncode, result.stderr[:500])
        return {'success': False, 'error': '경로 추적 결과를 받지 못했습니다.', 'hops': []}

    try:
        json_data = json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        current_app.logger.warning('traceroute JSON parse failed: %s; stdout=%r', e, result.stdout[:500])
        return {'success': False, 'error': '결과 파싱 실패', 'hops': []}

    if 'error' in json_data and not json_data.get('hops'):
        return {'success': False, 'error': json_data['error'], 'hops': []}

    hops = []
    for hop in json_data.get('hops', []):
        rtts = [p['rtt_ms'] for p in hop.get('probes', [])
                if p.get('success') and p.get('rtt_ms') is not None]
        hops.append({
            'ttl': hop.get('ttl'),
            'ip': hop.get('ip_address'),
            'hostname': hop.get('hostname'),
            'rtts': rtts,
            'latitude': hop.get('latitude'),
            'longitude': hop.get('longitude'),
            'country': hop.get('country'),
            'status': 'ok' if hop.get('status') != 'timeout' else 'timeout',
        })

    return {
        'success': True,
        'target': json_data.get('target_host'),
        'target_ip': json_data.get('target_ip'),
        'hops': hops,
    }


@route_bp.route('/route')
def route_page():
    return render_template('route.html')


@route_bp.route('/route/trace', methods=['POST'])
@limiter.limit("5 per minute; 30 per hour")
def trace():
    data = request.get_json(silent=True) or {}
    target = (data.get('target') or '').strip()

    if not target:
        return jsonify({'success': False, 'error': '대상 호스트를 입력해주세요.'}), 400
    if not _validate_target(target):
        return jsonify({'success': False, 'error': '잘못된 호스트명 형식입니다.'}), 400

    try:
        max_hops = max(1, min(int(data.get('max_hops', 30)), 30))
        probes = max(1, min(int(data.get('probes', 3)), 5))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': '잘못된 옵션 값.'}), 400

    # 기본 'all' — TCP + UDP + ICMP 3-way로 막힌 홉 뚫기 강화
    protocol = data.get('protocol', 'all')
    if protocol not in ('tcp', 'udp', 'icmp', 'both', 'all'):
        protocol = 'all'

    if not _trace_semaphore.acquire(blocking=False):
        return jsonify({
            'success': False,
            'error': '서버가 다른 추적을 처리 중입니다. 잠시 후 다시 시도해주세요.',
        }), 429
    try:
        result = run_tcp_traceroute(target, max_hops, probes, protocol)
    finally:
        _trace_semaphore.release()

    # 감사 로그: abuse 추적에 필요 (IP당 rate-limit 통과한 후의 실제 사용 기록)
    current_app.logger.info(
        'route.trace from=%s target=%s protocol=%s success=%s hops=%d',
        request.remote_addr, target, protocol,
        result.get('success'), len(result.get('hops', [])),
    )
    return jsonify(result)
