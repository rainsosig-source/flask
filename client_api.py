"""
클라이언트 전용 API - Flask Blueprint
리눅스 취약점 점검 클라이언트를 위한 REST API 엔드포인트
"""

import json
from flask import Blueprint, request, jsonify, Response

# 공통 DB 유틸리티 모듈 사용
from db_utils import get_db, DETECT_COMMANDS

# Blueprint 생성
client_bp = Blueprint('client', __name__)


# ==============================================================================
# API 엔드포인트
# ==============================================================================

@client_bp.route('/software', methods=['GET'])
def get_software_list():
    """
    모니터링 대상 소프트웨어 목록 조회
    
    Response:
        {
            "success": true,
            "data": [
                {"id": 1, "name": "Nginx", "keywords": ["nginx"], "detect_commands": ["nginx -v"]},
                ...
            ]
        }
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, name, keywords, enabled 
            FROM monitor_targets 
            WHERE enabled = TRUE 
            ORDER BY name
        """)
        
        targets = cursor.fetchall()
        cursor.close()
        conn.close()
        
        result = []
        for t in targets:
            keywords = t['keywords']
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except:
                    keywords = [keywords]
            
            result.append({
                'id': t['id'],
                'name': t['name'],
                'keywords': keywords,
                'detect_commands': DETECT_COMMANDS.get(t['name'], [])
            })
        
        return jsonify({
            'success': True,
            'data': result,
            'count': len(result)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@client_bp.route('/scan', methods=['POST'])
def scan_vulnerabilities():
    """
    설치된 소프트웨어 기반 취약점 스캔
    
    Request:
        {
            "software": [
                {"name": "nginx", "version": "1.18.0"},
                {"name": "mysql", "version": "8.0.32"},
                ...
            ]
        }
    
    Response:
        {
            "success": true,
            "data": [
                {
                    "cve_id": "CVE-2024-1234",
                    "software": "Nginx",
                    "severity": "CRITICAL",
                    "cvss_score": 9.8,
                    "description_ko": "...",
                    "simple_explanation": "...",
                    "has_verification": true
                },
                ...
            ],
            "summary": {
                "total": 5,
                "critical": 1,
                "high": 2,
                "medium": 2,
                "low": 0
            }
        }
    """
    try:
        data = request.get_json()
        
        if not data or 'software' not in data:
            return jsonify({
                'success': False,
                'error': 'software 필드가 필요합니다'
            }), 400
        
        software_list = data['software']
        
        if not software_list:
            return jsonify({
                'success': True,
                'data': [],
                'summary': {'total': 0, 'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
            })
        
        conn = get_db()
        cursor = conn.cursor()
        
        # 소프트웨어 이름으로 CVE 검색
        vulnerabilities = []
        software_names = [sw['name'].lower() for sw in software_list]
        
        # 모니터링 대상과 매칭
        cursor.execute("SELECT id, name, keywords FROM monitor_targets WHERE enabled = TRUE")
        targets = cursor.fetchall()
        
        matched_target_ids = []
        matched_names = {}
        
        for target in targets:
            keywords = target['keywords']
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except:
                    keywords = [keywords]
            
            # 클라이언트가 보낸 소프트웨어와 키워드 매칭
            for sw_name in software_names:
                for kw in keywords:
                    if kw.lower() in sw_name or sw_name in kw.lower():
                        matched_target_ids.append(target['id'])
                        matched_names[target['id']] = target['name']
                        break
        
        if matched_target_ids:
            # 매칭된 소프트웨어의 CVE 조회
            placeholders = ','.join(['%s'] * len(matched_target_ids))
            cursor.execute(f"""
                SELECT DISTINCT e.cve_id, e.severity, e.cvss_score, 
                       e.description_ko, e.description_en, e.simple_explanation,
                       e.affected_systems, e.remediation,
                       e.verification_code IS NOT NULL AND e.verification_code != '' as has_verification,
                       cs.target_id
                FROM cve_entries e
                JOIN cve_software cs ON e.cve_id = cs.cve_id
                WHERE cs.target_id IN ({placeholders})
                ORDER BY e.cvss_score DESC, e.published_date DESC
            """, matched_target_ids)
            
            cves = cursor.fetchall()
            
            for cve in cves:
                vulnerabilities.append({
                    'cve_id': cve['cve_id'],
                    'software': matched_names.get(cve['target_id'], 'Unknown'),
                    'severity': cve['severity'],
                    'cvss_score': float(cve['cvss_score']) if cve['cvss_score'] else 0.0,
                    'description_ko': cve['description_ko'] or cve['description_en'],
                    'simple_explanation': cve['simple_explanation'],
                    'affected_systems': cve['affected_systems'],
                    'remediation': cve['remediation'],
                    'has_verification': bool(cve['has_verification'])
                })
        
        cursor.close()
        conn.close()
        
        # 요약 통계
        summary = {
            'total': len(vulnerabilities),
            'critical': sum(1 for v in vulnerabilities if v['severity'] == 'CRITICAL'),
            'high': sum(1 for v in vulnerabilities if v['severity'] == 'HIGH'),
            'medium': sum(1 for v in vulnerabilities if v['severity'] == 'MEDIUM'),
            'low': sum(1 for v in vulnerabilities if v['severity'] == 'LOW')
        }
        
        return jsonify({
            'success': True,
            'data': vulnerabilities,
            'summary': summary
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@client_bp.route('/verify/<cve_id>', methods=['GET'])
def get_verification_code(cve_id):
    """
    CVE 검증 코드 조회
    
    Response:
        {
            "success": true,
            "data": {
                "cve_id": "CVE-2024-1234",
                "verification_code": "#!/usr/bin/env python3\\n...",
                "verification_description": "실행 방법 설명...",
                "language": "python"
            }
        }
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT cve_id, verification_code, verification_description
            FROM cve_entries
            WHERE cve_id = %s
        """, (cve_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not result:
            return jsonify({
                'success': False,
                'error': f'CVE {cve_id}를 찾을 수 없습니다'
            }), 404
        
        if not result['verification_code']:
            return jsonify({
                'success': False,
                'error': f'CVE {cve_id}에 대한 검증 코드가 없습니다'
            }), 404
        
        # 코드 언어 감지
        code = result['verification_code']
        if code.strip().startswith('#!/usr/bin/env python') or 'import ' in code:
            language = 'python'
        elif code.strip().startswith('#!/bin/bash') or code.strip().startswith('#!/bin/sh'):
            language = 'bash'
        else:
            language = 'unknown'
        
        return jsonify({
            'success': True,
            'data': {
                'cve_id': result['cve_id'],
                'verification_code': result['verification_code'],
                'verification_description': result['verification_description'],
                'language': language
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@client_bp.route('/report', methods=['POST'])
def submit_report():
    """
    클라이언트 스캔 결과 보고 (선택적)
    
    Request:
        {
            "hostname": "server-01",
            "ip_address": "192.168.1.100",
            "scan_time": "2024-12-13T11:30:00",
            "software": [...],
            "vulnerabilities": [...],
            "verification_results": [...]
        }
    """
    try:
        data = request.get_json()
        
        # TODO: 스캔 결과를 데이터베이스에 저장하거나 로깅
        # 현재는 간단히 수신 확인만 반환
        
        return jsonify({
            'success': True,
            'message': '스캔 결과가 접수되었습니다',
            'received_at': data.get('scan_time', 'unknown')
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@client_bp.route('/download', methods=['GET'])
def download_client():
    """
    클라이언트 스크립트 다운로드
    """
    # vuln_checker.py 내용을 직접 제공
    client_script = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
리눅스 취약점 점검 클라이언트
sosig.shop API를 사용하여 시스템의 CVE 취약점을 점검합니다.

Usage:
    python3 vuln_checker.py                    # 기본 실행
    python3 vuln_checker.py --json             # JSON 출력
    python3 vuln_checker.py --no-verify        # 검증 코드 실행 안함
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Dict, List

try:
    import requests
except ImportError:
    print("requests 패키지가 필요합니다: pip install requests")
    sys.exit(1)

DEFAULT_SERVER = "https://sosig.shop"
VERSION = "1.0.0"

class SoftwareDetector:
    DETECT_COMMANDS = {
        'apache http server': [('httpd -v', r'Apache/(\\d+\\.\\d+\\.\\d+)'), ('apache2 -v', r'Apache/(\\d+\\.\\d+\\.\\d+)')],
        'nginx': [('nginx -v', r'nginx/(\\d+\\.\\d+\\.\\d+)')],
        'mysql': [('mysql --version', r'(\\d+\\.\\d+\\.\\d+)')],
        'mariadb': [('mariadb --version', r'(\\d+\\.\\d+\\.\\d+)')],
        'postgresql': [('psql --version', r'(\\d+\\.\\d+\\.?\\d*)')],
        'php': [('php -v', r'PHP (\\d+\\.\\d+\\.\\d+)')],
        'redis': [('redis-server --version', r'v=(\\d+\\.\\d+\\.\\d+)')],
        'openssh': [('ssh -V', r'OpenSSH_(\\d+\\.\\d+)')],
        'openssl': [('openssl version', r'OpenSSL (\\d+\\.\\d+\\.\\d+)')],
        'sudo': [('sudo --version', r'Sudo version (\\d+\\.\\d+\\.?\\d*)')],
        'bash': [('bash --version', r'version (\\d+\\.\\d+\\.\\d+)')],
        'docker': [('docker --version', r'Docker version (\\d+\\.\\d+\\.\\d+)')],
        'node.js': [('node --version', r'v(\\d+\\.\\d+\\.\\d+)')],
        'python': [('python3 --version', r'Python (\\d+\\.\\d+\\.\\d+)')],
        'git': [('git --version', r'git version (\\d+\\.\\d+\\.\\d+)')],
    }
    
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.detected = []
    
    def detect_all(self):
        import re
        self.detected = []
        for sw_name, commands in self.DETECT_COMMANDS.items():
            for cmd, pattern in commands:
                try:
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
                    output = result.stdout + result.stderr
                    match = re.search(pattern, output, re.IGNORECASE)
                    if match:
                        self.detected.append({'name': sw_name, 'version': match.group(1), 'command': cmd})
                        if self.verbose: print(f"   ✓ {sw_name} {match.group(1)} 발견")
                        break
                except: continue
        return self.detected

class SafeCodeExecutor:
    DANGEROUS_PATTERNS = ['rm -rf', 'rm -f /', 'mkfs', 'dd if=', ':(){:|:&};:', 'chmod 777 /', 'shutdown', 'reboot']
    
    def __init__(self, timeout=30):
        self.timeout = timeout
    
    def execute(self, code, language='python'):
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.lower() in code.lower():
                return {'result': '오류', 'reason': f'위험한 패턴 감지: {pattern}', 'executed': False}
        
        suffix = '.py' if language == 'python' else '.sh'
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False, encoding='utf-8') as f:
                f.write(code)
                temp_file = f.name
            
            cmd = [sys.executable, temp_file] if language == 'python' else ['bash', temp_file]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
            output = result.stdout.strip()
            
            try:
                import re
                json_match = re.search(r'\\{[^{}]*"result"[^{}]*\\}', output)
                if json_match: return json.loads(json_match.group())
            except: pass
            return {'result': '완료', 'reason': output or result.stderr, 'executed': True}
        except subprocess.TimeoutExpired:
            return {'result': '타임아웃', 'reason': f'{self.timeout}초 타임아웃 초과', 'executed': False}
        except Exception as e:
            return {'result': '오류', 'reason': str(e), 'executed': False}
        finally:
            try: os.unlink(temp_file)
            except: pass

class VulnChecker:
    def __init__(self, server=DEFAULT_SERVER, verbose=True):
        self.server = server.rstrip('/')
        self.verbose = verbose
        self.detector = SoftwareDetector(verbose=verbose)
        self.executor = SafeCodeExecutor()
        self.software = []
        self.vulnerabilities = []
        self.verification_results = []
    
    def detect_software(self):
        if self.verbose: print("\\n🔍 시스템 스캔 중...")
        self.software = self.detector.detect_all()
        return self.software
    
    def scan_vulnerabilities(self):
        if not self.software: return []
        if self.verbose: print("\\n📋 취약점 조회 중...")
        try:
            response = requests.post(f"{self.server}/api/client/scan", json={"software": self.software}, timeout=30, headers={"Content-Type": "application/json"})
            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    self.vulnerabilities = data.get('data', [])
                    if self.verbose:
                        s = data.get('summary', {})
                        print(f"\\n📊 스캔 결과: 총 {s.get('total', 0)}개 취약점 발견")
                        print(f"   🔴 CRITICAL: {s.get('critical', 0)}, 🟠 HIGH: {s.get('high', 0)}, 🟡 MEDIUM: {s.get('medium', 0)}")
        except Exception as e:
            if self.verbose: print(f"   ✗ 오류: {e}")
        return self.vulnerabilities
    
    def run_verifications(self):
        if not self.vulnerabilities: return []
        if self.verbose: print("\\n🔧 검증 실행 중...")
        for vuln in self.vulnerabilities:
            if not vuln.get('has_verification'): continue
            try:
                response = requests.get(f"{self.server}/api/client/verify/{vuln['cve_id']}", timeout=10)
                if response.status_code == 200:
                    data = response.json().get('data', {})
                    result = self.executor.execute(data.get('verification_code', ''), data.get('language', 'python'))
                    result['cve_id'] = vuln['cve_id']
                    self.verification_results.append(result)
                    if self.verbose:
                        status = result.get('result', 'Unknown')
                        print(f"   {vuln['cve_id']}: {'⚠️ 취약' if status == '취약' else '✅ 양호' if status == '양호' else '❓ ' + status}")
                time.sleep(0.5)
            except: pass
        return self.verification_results
    
    def generate_report(self):
        return {'scan_time': datetime.now().isoformat(), 'software': self.software, 'vulnerabilities': self.vulnerabilities, 'verification_results': self.verification_results, 'summary': {'software_count': len(self.software), 'vulnerability_count': len(self.vulnerabilities), 'verified_count': len(self.verification_results), 'vulnerable_count': sum(1 for r in self.verification_results if r.get('result') == '취약')}}
    
    def print_table(self):
        if not self.vulnerabilities:
            print("\\n✅ 발견된 취약점이 없습니다.")
            return
        print("\\n" + "=" * 80)
        print(f"{'CVE ID':<18} {'심각도':<10} {'소프트웨어':<15} {'설명':<35}")
        print("=" * 80)
        for vuln in self.vulnerabilities[:20]:
            cve_id = vuln.get('cve_id', 'N/A')
            severity = vuln.get('severity', 'N/A')
            software = vuln.get('software', 'N/A')[:12]
            desc = (vuln.get('simple_explanation') or vuln.get('description_ko', ''))[:32]
            colors = {'CRITICAL': '\\033[91m', 'HIGH': '\\033[93m', 'MEDIUM': '\\033[33m', 'LOW': '\\033[94m'}
            c = colors.get(severity, '')
            print(f"{cve_id:<18} {c}{severity:<10}\\033[0m {software:<15} {desc}")
        print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="리눅스 취약점 점검 클라이언트 v" + VERSION)
    parser.add_argument('--server', '-s', default=DEFAULT_SERVER, help='API 서버 주소')
    parser.add_argument('--json', '-j', action='store_true', help='JSON 형식으로 결과 출력')
    parser.add_argument('--no-verify', action='store_true', help='검증 코드 실행하지 않음')
    args = parser.parse_args()
    verbose = not args.json
    if verbose:
        print(f"\\n{'='*60}\\n  🛡️  리눅스 취약점 점검 클라이언트 v{VERSION}\\n  📡 서버: {args.server}\\n{'='*60}")
    checker = VulnChecker(server=args.server, verbose=verbose)
    checker.detect_software()
    if not checker.software:
        if args.json: print(json.dumps({'error': '탐지된 소프트웨어가 없습니다'}, ensure_ascii=False))
        else: print("\\n⚠️ 탐지된 소프트웨어가 없습니다.")
        return
    checker.scan_vulnerabilities()
    if not args.no_verify and checker.vulnerabilities: checker.run_verifications()
    if args.json: print(json.dumps(checker.generate_report(), ensure_ascii=False, indent=2))
    else:
        checker.print_table()
        r = checker.generate_report()['summary']
        print(f"\\n📊 요약: 소프트웨어 {r['software_count']}개, 취약점 {r['vulnerability_count']}개, 검증 {r['verified_count']}개, 취약 {r['vulnerable_count']}개")
        print(f"{'='*60}\\n  점검 완료!\\n{'='*60}\\n")

if __name__ == '__main__':
    main()
'''
    
    return Response(
        client_script,
        mimetype='text/x-python',
        headers={'Content-Disposition': 'attachment; filename=vuln_checker.py'}
    )
