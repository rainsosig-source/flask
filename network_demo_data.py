"""네트워크 분석 페이지 데모 데이터 — 모두 가짜 (RFC 5737 documentation IP 사용)."""

DEMO_SUMMARY_MD = """## ⚠️ 이 페이지는 데모입니다

아래 모든 데이터는 합성된 **예시 데이터**이며 실제 네트워크 트래픽이 아닙니다. 호스트 IP는 `192.0.2.0/24` (RFC 5737 문서용 대역)이고, 외부 IP는 `198.51.100.0/24`입니다.

---

## LLM 분석 (qwen3.6:35b)

1. **위협 요약**: 1건 (의심 활동 발견)

2. **의심 도메인/IP**:
   - `c2-beacon-evil.example.bad`: 192.0.2.20 호스트에서 5분 주기 비정상 connection 발견. DGA 패턴 유사
   - `198.51.100.244`: 알려지지 않은 IP, 외부 클라우드 평판 정보 부족

3. **이상 패턴**:
   - `192.0.2.20`(Win PC)이 동일 외부 IP로 5분 간격 8회 단발 TCP 연결 — beacon 의심
   - 22:00~02:00 야간 시간대에 평소보다 3.5배 많은 DNS 질의

4. **다음 행동**:
   - `192.0.2.20` 프로세스 점검 (Sysinternals Autoruns / 백신 풀스캔)
   - `c2-beacon-evil.example.bad`를 라우터 또는 Pi-hole에서 차단
   - 24시간 추가 관찰

## 🔬 Claude Haiku 2차 분석 (의심 호스트 deep dive)

- 🚨 **`c2-beacon-evil.example.bad`** _[C2 의심]_ — 알려지지 않은 .bad TLD + 'c2-beacon' 명시 문자열. 정상 SaaS/CDN과 무관한 패턴. 즉시 차단 권장.
  - 권장: 라우터 DNS 블랙리스트 등록 + 192.0.2.20 디바이스 격리
- ⚠️ **`198.51.100.244`** _[알수없음]_ — 평판 정보 부족. 도메인 없이 IP 직접 통신은 일반 SaaS에서 드뭄.
  - 권장: WHOIS·AbuseIPDB 조회 후 결과 따라 추가 조치
"""

DEMO_DATA = {
    "report_date": "2026-05-12",
    "created_at": "2026-05-12 03:00 (DEMO)",
    "summary_md": DEMO_SUMMARY_MD,
    "stats_json": {
        "conn_total": 8421,
        "dns_total": 1247,
        "ssl_total": 3104,
        "real_alerts": 87,
        "noise_alerts": 412,
        "time_series": {
            "labels": [f"{h:02d}" for h in range(24)],
            "conn": [85, 62, 41, 38, 52, 78, 145, 218, 312, 398, 421, 467, 489, 512, 478, 442, 398, 365, 412, 451, 502, 578, 612, 543],
            "dns":  [12,  8,  5,  4,  7, 11,  22,  33,  47,  58,  62,  68,  71,  74,  69,  64,  58,  53,  60,  66,  73,  84,  89,  79],
            "ssl":  [31, 22, 14, 13, 19, 28,  53,  80, 115, 146, 155, 172, 180, 188, 176, 162, 146, 134, 152, 166, 184, 213, 225, 200],
        },
        "host_breakdown": {
            "192.0.2.10": {
                "conn": 3284, "bytes_out": 412_500_000, "bytes_in": 1_205_000_000,
                "top_remote_ips": [
                    ["198.51.100.10", 845], ["198.51.100.20", 612], ["198.51.100.30", 421],
                    ["198.51.100.40", 312], ["198.51.100.50", 198],
                ],
                "top_remote_geo": [
                    {"ip": "198.51.100.10", "count": 845, "lat": 37.7749, "lon": -122.4194, "country": "United States", "cc": "US", "isp": "Cloudflare", "as": "AS13335",
                     "by_hour": [25,18,12,10,12,18,28,42,55,72,80,88,92,95,88,80,68,58,62,68,75,85,92,82]},
                    {"ip": "198.51.100.20", "count": 612, "lat": 1.3521, "lon": 103.8198, "country": "Singapore", "cc": "SG", "isp": "AWS", "as": "AS16509",
                     "by_hour": [18,12,8,6,8,12,20,32,42,52,58,62,66,68,62,55,48,42,45,50,55,62,68,60]},
                    {"ip": "198.51.100.30", "count": 421, "lat": 35.6762, "lon": 139.6503, "country": "Japan", "cc": "JP", "isp": "Google", "as": "AS15169",
                     "by_hour": [12,8,5,4,5,8,14,22,30,38,42,45,48,50,46,42,36,30,33,36,40,45,50,42]},
                    {"ip": "198.51.100.40", "count": 312, "lat": 37.5665, "lon": 126.9780, "country": "South Korea", "cc": "KR", "isp": "Naver", "as": "AS9318",
                     "by_hour": [10,6,4,3,4,6,10,16,22,28,32,35,36,38,34,30,26,22,24,26,30,34,36,30]},
                    {"ip": "198.51.100.50", "count": 198, "lat": 47.6062, "lon": -122.3321, "country": "United States", "cc": "US", "isp": "Microsoft", "as": "AS8075",
                     "by_hour": [6,4,2,2,2,4,6,10,14,18,20,22,23,24,22,20,16,14,15,17,19,21,23,18]},
                ],
                "top_dns": [
                    ["api.example-ai.com", 412], ["telemetry.example.io", 298],
                    ["packages.example.org", 187], ["registry.example.dev", 142],
                    ["cdn.example.net", 88],
                ],
                "top_sni": [
                    ["api.example-ai.com", 1502], ["telemetry.example.io", 845],
                    ["packages.example.org", 412], ["registry.example.dev", 287],
                ],
                "verdict": "✅ API/패키지 미러/텔레메트리 - 개발 서버 정상 활동",
                "by_hour": [180, 140, 90, 70, 80, 110, 180, 240, 310, 380, 420, 450, 470, 490, 460, 430, 380, 340, 380, 410, 450, 510, 540, 480],
            },
            "192.0.2.20": {
                "conn": 1842, "bytes_out": 78_200_000, "bytes_in": 245_000_000,
                "top_remote_ips": [
                    ["198.51.100.244", 8], ["198.51.100.60", 312], ["198.51.100.70", 287],
                    ["198.51.100.80", 198], ["198.51.100.90", 142],
                ],
                "top_remote_geo": [
                    {"ip": "198.51.100.244", "count": 8, "lat": 55.7558, "lon": 37.6173, "country": "Russia", "cc": "RU", "isp": "Unknown VPS", "as": "AS9009",
                     "by_hour": [1,1,2,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0]},
                    {"ip": "198.51.100.60", "count": 312, "lat": 47.6062, "lon": -122.3321, "country": "United States", "cc": "US", "isp": "Microsoft", "as": "AS8075",
                     "by_hour": [2,1,1,0,1,1,3,8,15,22,26,28,30,32,28,24,18,14,16,20,24,28,32,28]},
                    {"ip": "198.51.100.70", "count": 287, "lat": 37.4419, "lon": -122.1430, "country": "United States", "cc": "US", "isp": "Google", "as": "AS15169",
                     "by_hour": [2,1,1,0,1,1,3,7,14,20,24,26,28,30,26,22,17,13,15,18,22,26,30,26]},
                    {"ip": "198.51.100.80", "count": 198, "lat": 51.5074, "lon": -0.1278, "country": "United Kingdom", "cc": "GB", "isp": "Cloudflare", "as": "AS13335",
                     "by_hour": [1,1,1,0,1,1,2,5,10,14,17,18,19,20,18,15,12,9,11,13,15,18,20,18]},
                    {"ip": "198.51.100.90", "count": 142, "lat": 53.5511, "lon": 9.9937, "country": "Germany", "cc": "DE", "isp": "Hetzner", "as": "AS24940",
                     "by_hour": [1,1,1,0,0,1,2,4,7,10,12,13,14,15,13,11,9,7,8,10,12,14,15,13]},
                ],
                "top_dns": [
                    ["c2-beacon-evil.example.bad", 8], ["update.example-os.com", 187],
                    ["browser.example.com", 142], ["mail.example.com", 88],
                ],
                "top_sni": [
                    ["update.example-os.com", 245], ["browser.example.com", 189],
                    ["mail.example.com", 112],
                ],
                "verdict": "⚠️ Win PC 정상 사용 + 5분 주기 beacon 의심 (c2-beacon-evil)",
                "by_hour": [12, 11, 50, 60, 55, 10, 5, 8, 45, 120, 145, 165, 170, 175, 160, 140, 125, 110, 130, 150, 168, 175, 162, 145],
            },
            "192.0.2.30": {
                "conn": 1247, "bytes_out": 12_500_000, "bytes_in": 8_400_000,
                "top_remote_ips": [
                    ["198.51.100.100", 412], ["198.51.100.110", 198],
                ],
                "top_dns": [
                    ["sync.example-nas.com", 287], ["update.example-nas.com", 142],
                ],
                "top_sni": [
                    ["sync.example-nas.com", 412], ["update.example-nas.com", 198],
                ],
                "verdict": "✅ NAS 동기화·업데이트 - 일상 운영",
                "by_hour": [52, 50, 48, 48, 50, 52, 55, 58, 60, 62, 60, 58, 56, 54, 52, 50, 52, 55, 58, 60, 56, 54, 52, 50],
            },
            "192.0.2.40": {
                "conn": 1284, "bytes_out": 4_200_000, "bytes_in": 2_800_000,
                "top_remote_ips": [
                    ["198.51.100.120", 412], ["198.51.100.130", 287],
                ],
                "top_dns": [
                    ["api.example-iot.com", 187], ["mqtt.example-iot.com", 142],
                ],
                "top_sni": [
                    ["api.example-iot.com", 287], ["mqtt.example-iot.com", 142],
                ],
                "verdict": "✅ IoT 게이트웨이 MQTT - 정상 데이터 전송",
                "by_hour": [54, 54, 54, 53, 54, 54, 54, 53, 54, 54, 53, 54, 54, 53, 54, 54, 53, 54, 54, 53, 54, 53, 54, 53],
            },
            "192.0.2.50": {
                "conn": 564, "bytes_out": 1_200_000, "bytes_in": 980_000,
                "top_remote_ips": [["198.51.100.140", 198]],
                "top_dns": [["captive.example.local", 87]],
                "top_sni": [["captive.example.local", 142]],
                "verdict": "❓ 게스트 디바이스 captive portal - 식별 미상",
                "by_hour": [0, 0, 0, 0, 0, 0, 0, 0, 12, 38, 52, 60, 65, 60, 55, 50, 45, 38, 32, 28, 22, 18, 12, 8],
            },
            "192.0.2.1": {
                "conn": 200, "bytes_out": 145_000, "bytes_in": 12_000,
                "top_remote_ips": [],
                "top_dns": [["1.2.0.192.in-addr.arpa", 88]],
                "top_sni": [],
                "verdict": "✅ 게이트웨이 역방향 DNS 조회만",
                "by_hour": [8, 8, 8, 8, 8, 8, 8, 8, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 9, 8, 8, 8, 8],
            },
        },
    },
}
