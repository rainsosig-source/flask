// 신뢰할 수 없는 문자열을 HTML에 안전하게 끼워 넣기 위한 이스케이프 헬퍼.
        // 사용자 입력(t.host)과 서버 응답(hop.hostname/ip/country, data.error 등)에 항상 적용한다.
        function escapeHtml(s) {
            if (s === null || s === undefined) return '';
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }

        let scene, camera, renderer, globe, pathLines = [], hopMarkers = [], controls;
        let cloudsMesh = null, sunLight = null, ambientLight = null;
        let cloudsEnabled = true, sunEnabled = true;

        // 애니메이션 관련 변수
        let animatedPackets = [];  // { mesh, points, progress, speed, color }
        let animationEnabled = true;

        // RTT 그래프 관련 변수
        let rttChart = null;
        let rttDatasets = [];

        // 추적 결과 저장용 (내보내기 기능)
        let traceResults = [];

        var colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899'];
        var colorIdx = 1;

        document.addEventListener('DOMContentLoaded', function () {
            var addBtn = document.getElementById('addBtn');
            if (addBtn) {
                addBtn.onclick = function () {
                    var list = document.getElementById('targetList');
                    var row = document.createElement('div');
                    row.className = 'target-row';
                    var colorInput = document.createElement('input');
                    colorInput.type = 'color';
                    colorInput.value = colors[colorIdx % colors.length];
                    colorIdx++;
                    var textInput = document.createElement('input');
                    textInput.type = 'text';
                    textInput.placeholder = 'example.com';
                    var removeBtn = document.createElement('button');
                    removeBtn.className = 'remove-btn';
                    removeBtn.textContent = 'x';
                    removeBtn.onclick = function () { row.remove(); };
                    row.appendChild(colorInput);
                    row.appendChild(textInput);
                    row.appendChild(removeBtn);
                    list.appendChild(row);
                };
            }
        });

        // 지구본(반지름 1) 전체가 화면에 들어가도록 카메라 거리 계산.

        // ─── SVG 방사형 경로 시각화 (지구본 대체) ───
        const SVG_NS = "http://www.w3.org/2000/svg";
        let svgRoot = null;
        let traceAngleCount = 0;

        function fitCameraToGlobe() { /* no-op for SVG */ }

        function initGlobe() {
            const container = document.getElementById('globe-container');
            container.innerHTML = '';
            svgRoot = document.createElementNS(SVG_NS, 'svg');
            svgRoot.setAttribute('viewBox', '-500 -500 1000 1000');
            svgRoot.setAttribute('preserveAspectRatio', 'xMidYMid meet');
            svgRoot.style.width = '100%';
            svgRoot.style.height = '100%';
            svgRoot.style.display = 'block';
            // 동심원 8개 (TTL 단계 — 4 TTL 당 1 ring)
            for (let i = 1; i <= 8; i++) {
                const r = i * 55;
                const ring = document.createElementNS(SVG_NS, 'circle');
                ring.setAttribute('cx', 0); ring.setAttribute('cy', 0); ring.setAttribute('r', r);
                ring.setAttribute('fill', 'none');
                ring.setAttribute('stroke', '#1f2937');
                ring.setAttribute('stroke-width', 0.5);
                ring.setAttribute('stroke-dasharray', i % 2 === 0 ? '0' : '4 4');
                ring.setAttribute('opacity', 0.55);
                svgRoot.appendChild(ring);
                // TTL 표시 (4 단위)
                if (i % 2 === 0) {
                    const lab = document.createElementNS(SVG_NS, 'text');
                    lab.setAttribute('x', 4); lab.setAttribute('y', -r + 3);
                    lab.setAttribute('fill', '#6e7681'); lab.setAttribute('font-size', 9);
                    lab.textContent = 'TTL ' + (i * 2);
                    svgRoot.appendChild(lab);
                }
            }
            // 중심 노드 (내 LAN / sosig.shop 서버)
            const centerGlow = document.createElementNS(SVG_NS, 'circle');
            centerGlow.setAttribute('cx', 0); centerGlow.setAttribute('cy', 0); centerGlow.setAttribute('r', 22);
            centerGlow.setAttribute('fill', '#58a6ff'); centerGlow.setAttribute('opacity', 0.18);
            svgRoot.appendChild(centerGlow);
            const center = document.createElementNS(SVG_NS, 'circle');
            center.setAttribute('cx', 0); center.setAttribute('cy', 0); center.setAttribute('r', 9);
            center.setAttribute('fill', '#58a6ff');
            svgRoot.appendChild(center);
            const cLabel = document.createElementNS(SVG_NS, 'text');
            cLabel.setAttribute('x', 0); cLabel.setAttribute('y', 26);
            cLabel.setAttribute('text-anchor', 'middle');
            cLabel.setAttribute('fill', '#c9d1d9'); cLabel.setAttribute('font-size', 11);
            cLabel.setAttribute('font-weight', 600);
            cLabel.textContent = '내 LAN / sosig.shop';
            svgRoot.appendChild(cLabel);
            container.appendChild(svgRoot);
            traceAngleCount = 0;
        }

        function animate() { /* no-op */ }
        function toggleClouds() { /* no-op */ }
        function toggleAnimation() { /* no-op */ }
        function toggleSunPosition() { /* no-op */ }

        function clearAllPaths() {
            if (!svgRoot) return;
            // 동심원/중심은 유지하고 trace 결과만 제거
            const traceEls = svgRoot.querySelectorAll('[data-trace]');
            traceEls.forEach(el => el.remove());
            traceAngleCount = 0;
        }

        function drawPath(hops) {
            drawPathColored(hops, '#3b82f6');
        }

        function createMarker() { /* no-op */ }
        function createArc() { /* no-op */ }

        async function startTrace() {
            var rows = document.querySelectorAll('#targetList .target-row');
            var targets = [];
            rows.forEach(function (row) {
                var colorInput = row.querySelector('input[type="color"]');
                var textInput = row.querySelector('input[type="text"]');
                var host = textInput.value.trim();
                if (host) {
                    targets.push({ host: host, color: colorInput.value });
                }
            });
            if (targets.length === 0) {
                alert('대상 호스트를 입력해주세요.');
                return;
            }
            var maxHops = parseInt(maxHopsInput.value) || 20;
            var probes = parseInt(probesInput.value) || 2;

            traceBtn.disabled = true;
            traceBtn.innerHTML = '<span class="spinner"></span>추적 중...';
            resultsDiv.innerHTML = '';
            clearPath();

            // 결과를 담을 pre 태그 미리 생성
            var pre = document.createElement('pre');
            pre.style.cssText = 'font-family:monospace;font-size:0.75rem;color:#a0a0b0;white-space:pre-wrap;padding:0.5rem;margin:0;';
            resultsDiv.appendChild(pre);

            // 로딩 메시지용 div 생성
            var loadingDiv = document.createElement('div');
            loadingDiv.className = 'loading-msg';
            resultsDiv.appendChild(loadingDiv);

            // 멀티타겟 동시 실행 (서버 세마포어가 동시 2건으로 제한하므로 클라도 2로 맞춤)
            var concurrency = Math.min(2, targets.length);
            var done = 0;
            function refreshLoading() {
                loadingDiv.textContent = '추적 중... (' + done + '/' + targets.length + ')';
            }
            refreshLoading();

            async function runOne(t) {
                var controller = new AbortController();
                // 백엔드 60s + 약간의 여유 (네트워크 왕복).
                var timeoutId = setTimeout(function () { controller.abort(); }, 65000);
                try {
                    var res = await fetch('/route/trace', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ target: t.host, max_hops: maxHops, probes: probes }),
                        signal: controller.signal
                    });
                    clearTimeout(timeoutId);
                    if (res.status === 429) {
                        showError(t.host + ': 요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.');
                        return;
                    }
                    if (!res.ok) {
                        throw new Error('서버 응답 오류: ' + res.status);
                    }
                    var data = await res.json();
                    if (data.success) {
                        displayResults(data, t.color, pre);
                        drawPathColored(data.hops, t.color);
                        addRttData(data.hops, t.host, t.color);
                        traceResults.push({ target: t.host, color: t.color, data: data });
                    } else {
                        showError(t.host + ': ' + (data.error || '추적 실패'));
                    }
                } catch (e) {
                    clearTimeout(timeoutId);
                    console.error(t.host, e);
                    if (e.name === 'AbortError') {
                        showError(t.host + ': 요청 시간 초과 (65초)');
                    } else {
                        showError(t.host + ': ' + (e.message || '네트워크 오류'));
                    }
                } finally {
                    done++;
                    refreshLoading();
                }
            }

            // 단순 N-병렬 풀: idx를 공유 카운터로 사용
            var idx = 0;
            async function worker() {
                while (idx < targets.length) {
                    var myIdx = idx++;
                    await runOne(targets[myIdx]);
                }
            }
            var workers = [];
            for (var w = 0; w < concurrency; w++) workers.push(worker());
            await Promise.all(workers);
            // RTT 그래프 업데이트
            updateRttChart();
            // 내보내기 버튼 표시
            if (traceResults.length > 0) {
                document.getElementById('exportSection').style.display = 'block';
            }
            // 로딩 메시지 제거
            loadingDiv.remove();
            traceBtn.disabled = false;
            traceBtn.innerHTML = '🔍 경로 추적 시작';
        }

        // 에러 메시지 표시
        function showError(message) {
            var errorDiv = document.createElement('div');
            errorDiv.className = 'error-toast';
            errorDiv.textContent = '⚠️ ' + message;
            resultsDiv.insertBefore(errorDiv, resultsDiv.firstChild);
        }

        // JSON 내보내기
        function exportJSON() {
            var dataStr = JSON.stringify(traceResults, null, 2);
            var blob = new Blob([dataStr], { type: 'application/json' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'traceroute_' + new Date().toISOString().slice(0, 10) + '.json';
            a.click();
            URL.revokeObjectURL(url);
        }

        // CSV 내보내기
        function exportCSV() {
            var csv = 'Target,Hop,IP,Hostname,Country,RTT(ms),Latitude,Longitude\n';
            traceResults.forEach(function (result) {
                var target = result.target;
                result.data.hops.forEach(function (hop) {
                    var rtt = (hop.rtts && hop.rtts.length > 0) ? hop.rtts[0].toFixed(1) : '';
                    csv += [target, hop.ttl, hop.ip || '*', hop.hostname || '', hop.country || '', rtt, hop.latitude || '', hop.longitude || ''].join(',') + '\n';
                });
            });
            var blob = new Blob([csv], { type: 'text/csv' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'traceroute_' + new Date().toISOString().slice(0, 10) + '.csv';
            a.click();
            URL.revokeObjectURL(url);
        }

        // RTT 데이터 추가
        function addRttData(hops, host, colorHex) {
            var rttValues = [];
            var labels = [];
            hops.forEach(function (hop) {
                labels.push('Hop ' + hop.ttl);
                if (hop.rtts && hop.rtts.length > 0) {
                    // 평균 RTT 계산
                    var avgRtt = hop.rtts.reduce(function (a, b) { return a + b; }, 0) / hop.rtts.length;
                    rttValues.push(avgRtt);
                } else {
                    rttValues.push(null); // 타임아웃
                }
            });

            rttDatasets.push({
                label: host,
                data: rttValues,
                borderColor: colorHex,
                backgroundColor: colorHex + '33',
                borderWidth: 2,
                pointRadius: 4,
                pointBackgroundColor: colorHex,
                tension: 0.3,
                spanGaps: true
            });
        }

        // RTT 차트 업데이트
        function updateRttChart() {
            if (rttDatasets.length === 0) return;

            var graphSection = document.getElementById('rttGraphSection');
            graphSection.style.display = 'block';

            // 최대 홉 수 찾기
            var maxHops = 0;
            rttDatasets.forEach(function (ds) {
                if (ds.data.length > maxHops) maxHops = ds.data.length;
            });

            // 라벨 생성
            var labels = [];
            for (var i = 1; i <= maxHops; i++) {
                labels.push(i);
            }

            // 통계 계산
            var allRtts = [];
            rttDatasets.forEach(function (ds) {
                ds.data.forEach(function (v) {
                    if (v !== null) allRtts.push(v);
                });
            });
            var avgRtt = allRtts.length > 0 ? (allRtts.reduce(function (a, b) { return a + b; }, 0) / allRtts.length) : 0;
            var maxRtt = allRtts.length > 0 ? Math.max.apply(null, allRtts) : 0;

            // 통계 표시
            document.getElementById('rttStats').innerHTML =
                '<div class="rtt-stat"><span class="rtt-stat-label">평균:</span><span class="rtt-stat-value avg">' + avgRtt.toFixed(1) + 'ms</span></div>' +
                '<div class="rtt-stat"><span class="rtt-stat-label">최대:</span><span class="rtt-stat-value max">' + maxRtt.toFixed(1) + 'ms</span></div>';

            // 레전드: ds.label은 호스트명(사용자 입력) → 반드시 escape.
            // borderColor는 자체 생성된 색상이지만 안전하게 검증.
            var legendHtml = '';
            rttDatasets.forEach(function (ds) {
                var color = (typeof ds.borderColor === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(ds.borderColor)) ? ds.borderColor : '#888';
                legendHtml += '<div class="rtt-legend-item"><span class="rtt-legend-color" style="background:' + color + '"></span>' + escapeHtml(ds.label) + '</div>';
            });
            document.getElementById('rttLegend').innerHTML = legendHtml;

            // 차트 생성/업데이트
            var ctx = document.getElementById('rttChart').getContext('2d');

            if (rttChart) {
                rttChart.destroy();
            }

            rttChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: rttDatasets
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {
                        intersect: false,
                        mode: 'index'
                    },
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            backgroundColor: 'rgba(0, 0, 0, 0.8)',
                            titleColor: '#fff',
                            bodyColor: '#fff',
                            padding: 10,
                            displayColors: true,
                            callbacks: {
                                label: function (context) {
                                    if (context.raw === null) return context.dataset.label + ': timeout';
                                    return context.dataset.label + ': ' + context.raw.toFixed(1) + 'ms';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            title: {
                                display: true,
                                text: 'Hop',
                                color: '#a0a0b0',
                                font: { size: 10 }
                            },
                            ticks: {
                                color: '#a0a0b0',
                                font: { size: 9 }
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.05)'
                            }
                        },
                        y: {
                            title: {
                                display: true,
                                text: 'RTT (ms)',
                                color: '#a0a0b0',
                                font: { size: 10 }
                            },
                            ticks: {
                                color: '#a0a0b0',
                                font: { size: 9 }
                            },
                            grid: {
                                color: 'rgba(255, 255, 255, 0.05)'
                            },
                            beginAtZero: true
                        }
                    }
                }
            });
        }

        // clearPath 시 RTT 데이터도 초기화
        var originalClearPath = clearPath;
        clearPath = function () {
            originalClearPath();
            rttDatasets = [];
            traceResults = [];
            if (rttChart) {
                rttChart.destroy();
                rttChart = null;
            }
            document.getElementById('rttGraphSection').style.display = 'none';
            document.getElementById('exportSection').style.display = 'none';
        };

        function drawPathColored(hops, colorHex) {
            if (!svgRoot) initGlobe();
            const validHops = hops.filter(h => h.ip);
            if (!validHops.length) return;
            traceAngleCount++;
            const angleDeg = -90 + (traceAngleCount - 1) * 47;
            const angleRad = angleDeg * Math.PI / 180;
            const maxRing = 8 * 55;
            const maxTtl = Math.max(16, ...validHops.map(h => h.ttl || 0));

            const g = document.createElementNS(SVG_NS, 'g');
            g.setAttribute('data-trace', traceAngleCount);
            svgRoot.appendChild(g);

            let prevX = 0, prevY = 0;
            validHops.forEach((hop, idx) => {
                const ttl = hop.ttl || (idx + 1);
                const r = (ttl / maxTtl) * maxRing;
                const x = r * Math.cos(angleRad);
                const y = r * Math.sin(angleRad);
                const line = document.createElementNS(SVG_NS, 'line');
                line.setAttribute('x1', prevX); line.setAttribute('y1', prevY);
                line.setAttribute('x2', x); line.setAttribute('y2', y);
                line.setAttribute('stroke', colorHex);
                line.setAttribute('stroke-width', 1.8);
                line.setAttribute('opacity', 0.7);
                g.appendChild(line);
                const dot = document.createElementNS(SVG_NS, 'circle');
                dot.setAttribute('cx', x); dot.setAttribute('cy', y);
                dot.setAttribute('r', 4.5);
                dot.setAttribute('fill', colorHex);
                dot.setAttribute('data-ttl', ttl);
                dot.setAttribute('data-ip', hop.ip || '');
                dot.style.cursor = 'pointer';
                const t = document.createElementNS(SVG_NS, 'title');
                const rtts = (hop.rtts || []).filter(v => typeof v === 'number');
                const avg = rtts.length ? (rtts.reduce((a,b)=>a+b,0)/rtts.length).toFixed(1) : '-';
                t.textContent = `TTL ${ttl} · ${hop.ip || 'timeout'}${hop.hostname ? ' (' + hop.hostname + ')' : ''} · ${avg}ms`;
                dot.appendChild(t);
                g.appendChild(dot);
                prevX = x; prevY = y;
            });
            const last = validHops[validHops.length - 1];
            if (last) {
                const dest = document.createElementNS(SVG_NS, 'circle');
                dest.setAttribute('cx', prevX); dest.setAttribute('cy', prevY);
                dest.setAttribute('r', 9);
                dest.setAttribute('fill', 'none');
                dest.setAttribute('stroke', colorHex);
                dest.setAttribute('stroke-width', 2);
                g.appendChild(dest);
                const lbl = document.createElementNS(SVG_NS, 'text');
                const ox = prevX + (prevX >= 0 ? 14 : -14);
                const oy = prevY + 4;
                lbl.setAttribute('x', ox); lbl.setAttribute('y', oy);
                lbl.setAttribute('text-anchor', prevX >= 0 ? 'start' : 'end');
                lbl.setAttribute('fill', colorHex);
                lbl.setAttribute('font-size', 11);
                lbl.setAttribute('font-weight', 600);
                lbl.textContent = last.hostname || last.ip;
                g.appendChild(lbl);
            }
        }

        function displayResults(data, colorHex, pre) {
            var hops = data.hops || [];
            // pre가 전달되지 않으면 기존 방식으로 찾거나 생성
            if (!pre) {
                pre = document.getElementById('results').querySelector('pre');
                if (!pre) {
                    pre = document.createElement('pre');
                    pre.style.cssText = 'font-family:monospace;font-size:0.75rem;color:#a0a0b0;white-space:pre-wrap;padding:0.5rem;margin:0;';
                    document.getElementById('results').innerHTML = '';
                    document.getElementById('results').appendChild(pre);
                }
            }
            // 안전한 색상 토큰만 허용 (#RRGGBB / #RGB)
            var safeColor = (typeof colorHex === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(colorHex)) ? colorHex : '';
            var line = '';
            if (safeColor) {
                line += '<span style="color:' + safeColor + '">■</span> ';
            }
            line += 'traceroute to ' + escapeHtml(data.target) + ' (' + escapeHtml(data.target_ip || '') + ')\n';
            hops.forEach(function (hop) {
                var rttText = '* * *';
                var rttClass = '';
                if (hop.rtts && hop.rtts.length > 0) {
                    var avgRtt = hop.rtts.reduce(function (a, b) { return a + b; }, 0) / hop.rtts.length;
                    rttText = hop.rtts.map(function (r) { return r.toFixed(1) + 'ms'; }).join(' ');
                    if (avgRtt >= 200) {
                        rttClass = 'rtt-critical';
                    } else if (avgRtt >= 100) {
                        rttClass = 'rtt-high';
                    }
                }
                var host = hop.hostname || hop.ip || '*';
                var country = hop.country ? ' [' + escapeHtml(hop.country) + ']' : '';
                // 모든 사용자/서버 문자열은 escape 후 삽입.
                var safeHost = escapeHtml(host).replace(/\\n/g, '\n');
                var safeRtt = escapeHtml(rttText);
                if (rttClass) {
                    line += '  ' + hop.ttl + '  ' + safeHost + country + '  <span class="' + rttClass + '">' + safeRtt + '</span>\n';
                } else {
                    line += '  ' + hop.ttl + '  ' + safeHost + country + '  ' + safeRtt + '\n';
                }
            });
            line = line.replace(/\n{2,}/g, '\n');
            line += '\n';
            pre.innerHTML += line;
        }

        function focusHop(idx, lat, lon) {
            document.querySelectorAll('.hop-item').forEach(el => el.classList.remove('focus'));
            const target = document.querySelector('.hop-item[data-idx="' + idx + '"]');
            if (target) {
                target.classList.add('focus');
                target.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
