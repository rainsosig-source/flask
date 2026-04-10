function log(msg) {
            console.log(msg);
        }

        const API_CALENDAR = '/api/calendar-data';
        const API_EPISODES = '/api/episodes';
        const API_KEYWORDS = '/api/keywords';

        let currentDate = new Date();
        let selectedDate = null;
        let selectedKeyword = null;
        let selectedHour = null;
        let episodeCounts = {};
        let currentEpisodes = [];
        let filteredEpisodes = [];

        // Player State
        const audio = document.getElementById('audioPlayer');
        const playerBar = document.getElementById('playerBar');
        const mainPlayBtn = document.getElementById('mainPlayBtn');
        const progressFill = document.getElementById('progressFill');
        const progressBarWrapper = document.getElementById('progressBarWrapper');
        const currentTimeEl = document.getElementById('currentTime');
        const durationEl = document.getElementById('duration');
        const visualizer = document.getElementById('visualizer');
        const playerTitle = document.getElementById('playerTitle');
        const playerMeta = document.getElementById('playerMeta');

        async function init() {
            log('Init started');
            await fetchKeywords();
            await fetchCalendarData();

            // Auto-select today's date
            const now = new Date();
            const year = now.getFullYear();
            const month = String(now.getMonth() + 1).padStart(2, '0');
            const day = String(now.getDate()).padStart(2, '0');
            selectedDate = `${year}-${month}-${day}`;

            fetchEpisodes(selectedDate);
            renderCalendar();
        }

        async function fetchKeywords() {
            try {
                const res = await fetch(API_KEYWORDS);
                const keywords = await res.json();
                const container = document.getElementById('keywordTags');
                container.innerHTML = '<div class="tag active" onclick="filterKeyword(null, this)">All</div>';
                keywords.forEach(k => {
                    const tag = document.createElement('div');
                    tag.className = 'tag';
                    tag.innerText = k.topic || k.keyword;
                    tag.onclick = () => filterKeyword(k.id, tag);
                    container.appendChild(tag);
                });
            } catch (err) { log('Err keywords: ' + err); }
        }

        async function fetchCalendarData() {
            let url = API_CALENDAR;
            if (selectedKeyword) url += `?keyword_id=${selectedKeyword}`;
            try {
                const res = await fetch(url);
                episodeCounts = await res.json();
                renderCalendar();
            } catch (err) { log('Err calendar: ' + err); }
        }

        async function fetchEpisodes(dateStr) {
            let url = API_EPISODES + '?';
            if (dateStr) url += `date=${dateStr}&`;
            if (selectedKeyword) url += `keyword_id=${selectedKeyword}&`;

            const listHeader = document.getElementById('listHeader');
            listHeader.innerText = dateStr ? `Episodes for ${dateStr}` : 'Recent Episodes';

            try {
                const res = await fetch(url);
                currentEpisodes = await res.json();

                // Reset time filter when fetching new episodes
                selectedHour = null;
                updateTimeFilterUI();

                filterEpisodes();
            } catch (err) { log('Err episodes: ' + err); }
        }

        function updateTimeFilterUI() {
            const panel = document.getElementById('timeFilterPanel');
            const container = document.getElementById('timeTags');

            if (!currentEpisodes || currentEpisodes.length === 0) {
                panel.style.display = 'none';
                return;
            }

            // Extract unique HOURS
            const hours = new Set();
            currentEpisodes.forEach(ep => {
                // created_at format: YYYY-MM-DD HH:MM:SS
                const timePart = ep.created_at.split(' ')[1]; // HH:MM:SS
                const h = timePart.substring(0, 2); // HH
                hours.add(h);
            });

            if (hours.size <= 1) {
                panel.style.display = 'none'; // Don't show if only 1 hour group
                return;
            }

            panel.style.display = 'block';
            container.innerHTML = '<div class="tag active" onclick="filterTime(null, this)">All</div>';

            Array.from(hours).sort().forEach(h => {
                const tag = document.createElement('div');
                tag.className = 'tag';
                tag.innerText = `${h}시`; // Format: 09시
                tag.onclick = () => filterTime(h, tag);
                container.appendChild(tag);
            });
        }

        function filterEpisodes() {
            if (selectedHour) {
                filteredEpisodes = currentEpisodes.filter(ep => {
                    const timePart = ep.created_at.split(' ')[1];
                    return timePart.startsWith(selectedHour);
                });
            } else {
                filteredEpisodes = currentEpisodes;
            }
            renderEpisodeList();
        }

        function renderCalendar() {
            const year = currentDate.getFullYear();
            const month = currentDate.getMonth();
            document.getElementById('currentMonth').innerText = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'long' }).format(currentDate);

            const firstDay = new Date(year, month, 1).getDay();
            const lastDate = new Date(year, month + 1, 0).getDate();
            const grid = document.getElementById('calendarGrid');
            grid.innerHTML = '';

            for (let i = 0; i < firstDay; i++) grid.appendChild(createDayEl('empty'));

            for (let i = 1; i <= lastDate; i++) {
                const el = createDayEl('day');
                const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(i).padStart(2, '0')}`;

                el.innerHTML = `<span>${i}</span>`;

                if (episodeCounts[dateStr]) el.classList.add('has-data');
                if (dateStr === selectedDate) el.classList.add('selected');

                el.onclick = () => {
                    selectedDate = dateStr;
                    fetchEpisodes(dateStr);
                    renderCalendar();
                };
                grid.appendChild(el);
            }
        }

        function createDayEl(className) {
            const el = document.createElement('div');
            el.className = className;
            return el;
        }

        function renderEpisodeList() {
            const container = document.getElementById('episodeList');
            container.innerHTML = '';

            if (filteredEpisodes.length === 0) {
                container.innerHTML = `
                    <div class="empty-state">
                        <div class="empty-state-icon">📭</div>
                        <p>No episodes found.</p>
                    </div>`;
                return;
            }

            filteredEpisodes.forEach(ep => {
                const card = document.createElement('div');
                card.className = 'episode-card';
                if (audio.src.includes(ep.static_path)) card.classList.add('playing');

                card.onclick = () => playEpisode(ep, card);

                card.innerHTML = `
                    <div class="play-icon">▶</div>
                    <div class="episode-info">
                        <h3>${ep.title}</h3>
                        <p>
                            ${ep.press} 
                            <a href="${ep.link}" target="_blank" onclick="event.stopPropagation()" class="article-link">Read Article ↗</a>
                        </p>
                        <div class="episode-date">${ep.created_at}</div>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        function filterKeyword(id, tagEl) {
            selectedKeyword = id;
            document.querySelectorAll('#keywordTags .tag').forEach(t => t.classList.remove('active'));
            tagEl.classList.add('active');
            // Keep selected date when filtering by keyword, or reset? 
            // User asked for "today automatically selected", so let's keep it if set, or reset to today if null.
            // For now, let's just refresh data.
            fetchCalendarData();
            fetchEpisodes(selectedDate);
        }

        function filterTime(hour, tagEl) {
            selectedHour = hour;
            document.querySelectorAll('#timeTags .tag').forEach(t => t.classList.remove('active'));
            tagEl.classList.add('active');
            filterEpisodes();
        }

        document.getElementById('prevMonth').onclick = () => {
            currentDate.setMonth(currentDate.getMonth() - 1);
            renderCalendar();
        };

        document.getElementById('nextMonth').onclick = () => {
            currentDate.setMonth(currentDate.getMonth() + 1);
            renderCalendar();
        };

        // Player Logic
        function playEpisode(ep, cardEl) {
            document.querySelectorAll('.episode-card').forEach(c => c.classList.remove('playing'));
            if (cardEl) cardEl.classList.add('playing');

            const path = '/static/' + (ep.static_path.startsWith('/') ? ep.static_path.substring(1) : ep.static_path);
            audio.src = path;
            audio.playbackRate = speeds[speedIndex];
            audio.play();

            playerTitle.innerText = ep.title;
            playerMeta.innerText = ep.press;
            playerBar.classList.add('active');

            updatePlayBtn(true);
        }

        function togglePlay() {
            if (audio.paused) { audio.play(); updatePlayBtn(true); }
            else { audio.pause(); updatePlayBtn(false); }
        }

        function updatePlayBtn(isPlaying) {
            if (isPlaying) {
                mainPlayBtn.innerText = '⏸';
                mainPlayBtn.style.paddingLeft = '0';
                visualizer.classList.remove('paused');
            } else {
                mainPlayBtn.innerText = '▶';
                mainPlayBtn.style.paddingLeft = '4px';
                visualizer.classList.add('paused');
            }
        }

        function skip(seconds) { audio.currentTime += seconds; }

        const speeds = [1, 1.25, 1.5, 1.75, 2];
        let speedIndex = 0;
        function cycleSpeed() {
            speedIndex = (speedIndex + 1) % speeds.length;
            audio.playbackRate = speeds[speedIndex];
            const btn = document.getElementById('speedBtn');
            btn.innerText = speeds[speedIndex] + 'x';
            btn.classList.toggle('active', speedIndex !== 0);
        }

        audio.addEventListener("play", () => {
            audio.playbackRate = speeds[speedIndex];
        });

        audio.addEventListener('timeupdate', () => {
            const percent = (audio.currentTime / audio.duration) * 100;
            progressFill.style.width = `${percent}%`;
            currentTimeEl.innerText = formatTime(audio.currentTime);
            durationEl.innerText = formatTime(audio.duration || 0);
        });

        audio.addEventListener('ended', () => {
            updatePlayBtn(false);
            progressFill.style.width = '0%';
        });

        progressBarWrapper.addEventListener('click', (e) => {
            const rect = progressBarWrapper.getBoundingClientRect();
            const pos = (e.clientX - rect.left) / rect.width;
            audio.currentTime = pos * audio.duration;
        });

        function formatTime(seconds) {
            if (isNaN(seconds)) return "0:00";
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
        }


        // Theme toggle
        const currentTheme = localStorage.getItem('theme') || 'dark';
        const html = document.documentElement;
        if (currentTheme === 'light') {
            html.classList.add('light-mode');
            document.getElementById('themeIcon').textContent = '\u2600\uFE0F';
            document.getElementById('themeText').textContent = 'Light';
        }
        function toggleTheme() {
            html.classList.toggle('light-mode');
            if (html.classList.contains('light-mode')) {
                document.getElementById('themeIcon').textContent = '\u2600\uFE0F';
                document.getElementById('themeText').textContent = 'Light';
                localStorage.setItem('theme', 'light');
            } else {
                document.getElementById('themeIcon').textContent = '\uD83C\uDF19';
                document.getElementById('themeText').textContent = 'Dark';
                localStorage.setItem('theme', 'dark');
            }
        }

        init();
