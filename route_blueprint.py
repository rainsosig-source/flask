"""
Traceroute 웹 인터페이스 - Flask Blueprint
tcp_traceroute.py를 사용하여 경로 추적 + 3D 지구본 시각화
"""

import subprocess
import json
import re
from flask import Blueprint, render_template, request, jsonify

route_bp = Blueprint('route', __name__)


def run_tcp_traceroute(target: str, max_hops: int = 30, probes: int = 3, protocol: str = "both") -> dict:
    """tcp_traceroute.py를 실행하여 경로를 추적합니다."""
    # 입력 검증
    if not re.match(r'^[a-zA-Z0-9\.\-]+$', target):
        return {'success': False, 'error': '잘못된 호스트명입니다.', 'hops': []}
    
    try:
        cmd = [
            'python3', '/root/flask-app/tcp_traceroute.py',
            target,
            '-m', str(max_hops),
            '-q', str(probes),
            '-P', protocol,
            '--json'
            # 위경도 정보를 포함하기 위해 --no-location 제거
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        output = result.stdout
        
        # JSON 블록 추출 ("JSON 결과:" 이후)
        json_marker = 'JSON 결과:\n'
        json_start = output.find(json_marker)
        if json_start != -1:
            json_str = output[json_start + len(json_marker):].strip()
        else:
            json_start = output.rfind('{\n  "target_host"')
            if json_start == -1:
                return {'success': False, 'error': '결과 파싱 실패', 'hops': []}
            json_str = output[json_start:].strip()
        
        json_data = json.loads(json_str)
        
        # 홉 데이터 변환 (위경도 포함)
        hops = []
        for hop in json_data.get('hops', []):
            rtts = []
            for probe in hop.get('probes', []):
                if probe.get('success') and probe.get('rtt_ms') is not None:
                    rtts.append(probe['rtt_ms'])
            
            hops.append({
                'ttl': hop.get('ttl'),
                'ip': hop.get('ip_address'),
                'hostname': hop.get('hostname'),
                'rtts': rtts,
                'latitude': hop.get('latitude'),
                'longitude': hop.get('longitude'),
                'country': hop.get('country'),
                'status': 'ok' if hop.get('status') != 'timeout' else 'timeout'
            })
        
        return {
            'success': True,
            'target': json_data.get('target_host'),
            'target_ip': json_data.get('target_ip'),
            'hops': hops
        }
        
    except subprocess.TimeoutExpired:
        return {'success': False, 'error': '시간 초과 (3분)', 'hops': []}
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'JSON 파싱 오류: {e}', 'hops': []}
    except Exception as e:
        return {'success': False, 'error': str(e), 'hops': []}


@route_bp.route('/route')
def route_page():
    return render_template('route.html')


@route_bp.route('/route/trace', methods=['POST'])
def trace():
    data = request.get_json() or {}
    target = data.get('target', '').strip()
    max_hops = min(int(data.get('max_hops', 30)), 30)
    probes = min(int(data.get('probes', 3)), 5)
    protocol = data.get('protocol', 'both')
    
    if protocol not in ('tcp', 'udp', 'both'):
        protocol = 'both'
    
    if not target:
        return jsonify({'success': False, 'error': '대상 호스트를 입력해주세요.'})
    
    result = run_tcp_traceroute(target, max_hops, probes, protocol)
    return jsonify(result)


@route_bp.route('/route/api/<target>')
def trace_api(target: str):
    max_hops = min(int(request.args.get('max_hops', 30)), 30)
    probes = min(int(request.args.get('probes', 3)), 5)
    protocol = request.args.get('protocol', 'both')
    result = run_tcp_traceroute(target, max_hops, probes, protocol)
    return jsonify(result)
