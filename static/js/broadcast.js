const PER_PAGE = 50;
        let currentPage = 1;
        let currentTopic = null;
        let searchQuery = '';
        let searchTimer = null;
        let allKeywords = [];

        const audio = document.getElementById('audioPlayer');
        const miniPlayer = document.getElementById('miniPlayer');
        const speeds = [1, 1.25, 1.5, 1.75, 2];
        let speedIndex = 0;

        async function init() {
            const theme = localStorage.getItem('theme');
            if (theme === 'light') {
                document.documentElement.classList.add('light-mode');
                document.getElementById('themeBtn').textContent = '☀️';
            }
            await fetchKeywords();
            await fetchEpisodes();
        }

        async function fetchKeywords() {
            try {
                const res = await fetch('/api/keywords');
                allKeywords = await res.json();
                renderTopicFilters();
                renderStats();
            } catch (e) { console.error(e); }
        }

        function renderTopicFilters() {
            const container = document.getElementById('topicFilters');
            container.innerHTML = '';
            allKeywords.forEach(k => {
                const btn = document.createElement('button');
                btn.className = 'filter-btn';
                btn.innerText = k.topic || k.keyword;
                btn.onclick = () => filterTopic(k.id, btn);
                container.appendChild(btn);
            });
        }

        async function renderStats() {
            try {
                const res = await fetch('/api/broadcast/stats');
                const stats = await res.json();
                const bar = document.getElementById('statsBar');
                bar.innerHTML = `
                    <div class="stat-card">
                        <div class="stat-label">Total Episodes</div>
                        <div class="stat-value">${stats.total.toLocaleString()}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Today</div>
                        <div class="stat-value">${stats.today}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Topics</div>
                        <div class="stat-value">${stats.topics}</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Sources</div>
                        <div class="stat-value">${stats.sources}</div>
                    </div>
                `;
            } catch (e) { console.error(e); }
        }

        async function fetchEpisodes() {
            let url = `/api/broadcast?page=${currentPage}&per_page=${PER_PAGE}`;
            if (currentTopic) url += `&keyword_id=${currentTopic}`;
            if (searchQuery) url += `&q=${encodeURIComponent(searchQuery)}`;

            try {
                const res = await fetch(url);
                const data = await res.json();
                renderTable(data.episodes);
                renderPagination(data.total, data.page, data.per_page);
            } catch (e) { console.error(e); }
        }

        function renderTable(episodes) {
            const tbody = document.getElementById('tableBody');
            const empty = document.getElementById('emptyState');

            if (!episodes || episodes.length === 0) {
                tbody.innerHTML = '';
                empty.style.display = 'block';
                return;
            }
            empty.style.display = 'none';

            tbody.innerHTML = episodes.map(ep => {
                const isEconomy = (ep.topic || '').startsWith('경제');
                const badgeClass = isEconomy ? 'topic-badge economy' : 'topic-badge';
                const date = ep.created_at.replace(' ', '<br>');
                return `
                <tr onclick="playEpisode(this, '${ep.static_path}', '${ep.title.replace(/'/g, "\\'")}', '${ep.press}')">
                    <td class="td-play"><button class="play-btn" onclick="event.stopPropagation();">▶</button></td>
                    <td class="td-title"><a href="${ep.link}" target="_blank" onclick="event.stopPropagation();">${ep.title}</a></td>
                    <td class="td-topic"><span class="${badgeClass}">${ep.topic || '-'}</span></td>
                    <td class="td-press">${ep.press}</td>
                    <td class="td-date">${ep.created_at}</td>
                </tr>`;
            }).join('');
        }

        function renderPagination(total, page, perPage) {
            const totalPages = Math.ceil(total / perPage);
            const container = document.getElementById('pagination');
            if (totalPages <= 1) { container.innerHTML = ''; return; }

            let html = `<button class="page-btn" onclick="goPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>&lsaquo;</button>`;

            let start = Math.max(1, page - 3);
            let end = Math.min(totalPages, page + 3);

            if (start > 1) html += `<button class="page-btn" onclick="goPage(1)">1</button><span style="color:var(--text-muted);padding:0 5px;">...</span>`;

            for (let i = start; i <= end; i++) {
                html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="goPage(${i})">${i}</button>`;
            }

            if (end < totalPages) html += `<span style="color:var(--text-muted);padding:0 5px;">...</span><button class="page-btn" onclick="goPage(${totalPages})">${totalPages}</button>`;

            html += `<button class="page-btn" onclick="goPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>&rsaquo;</button>`;
            container.innerHTML = html;
        }

        function goPage(p) {
            currentPage = p;
            fetchEpisodes();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        function filterTopic(id, btn) {
            currentTopic = id;
            currentPage = 1;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            fetchEpisodes();
        }

        function debounceSearch() {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => {
                searchQuery = document.getElementById('searchInput').value.trim();
                currentPage = 1;
                fetchEpisodes();
            }, 400);
        }

        // Player
        function playEpisode(row, path, title, press) {
            document.querySelectorAll('tbody tr').forEach(r => r.style.background = '');
            row.style.background = 'rgba(76, 201, 240, 0.08)';

            const src = '/static/' + (path.startsWith('/') ? path.substring(1) : path);
            audio.src = src;
            audio.playbackRate = speeds[speedIndex];
            audio.play();

            document.getElementById('miniTitle').innerText = title;
            document.getElementById('miniMeta').innerText = press;
            document.getElementById('miniPlayBtn').innerText = '⏸';
            miniPlayer.classList.add('active');
        }

        function togglePlay() {
            if (audio.paused) { audio.play(); document.getElementById('miniPlayBtn').innerText = '⏸'; }
            else { audio.pause(); document.getElementById('miniPlayBtn').innerText = '▶'; }
        }

        function cycleSpeed() {
            speedIndex = (speedIndex + 1) % speeds.length;
            audio.playbackRate = speeds[speedIndex];
            const btn = document.getElementById('miniSpeedBtn');
            btn.innerText = speeds[speedIndex] + 'x';
            btn.classList.toggle('active', speedIndex !== 0);
        }

        audio.addEventListener('timeupdate', () => {
            const pct = (audio.currentTime / audio.duration) * 100;
            document.getElementById('miniProgressFill').style.width = pct + '%';
            const fmt = s => { if (isNaN(s)) return '0:00'; const m = Math.floor(s/60); return m + ':' + String(Math.floor(s%60)).padStart(2,'0'); };
            document.getElementById('miniTime').innerText = fmt(audio.currentTime) + ' / ' + fmt(audio.duration);
        });

        audio.addEventListener('ended', () => {
            document.getElementById('miniPlayBtn').innerText = '▶';
        });

        document.getElementById('miniProgressBar').addEventListener('click', e => {
            const rect = e.currentTarget.getBoundingClientRect();
            audio.currentTime = ((e.clientX - rect.left) / rect.width) * audio.duration;
        });

        function toggleTheme() {
            document.documentElement.classList.toggle('light-mode');
            const isLight = document.documentElement.classList.contains('light-mode');
            document.getElementById('themeBtn').textContent = isLight ? '☀️' : '🌙';
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
        }

        init();
