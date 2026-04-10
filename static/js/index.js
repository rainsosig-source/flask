// Visit counter
        fetch('/api/visit', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({page: '/'})
        })
        .then(r => r.json())
        .then(d => document.getElementById('visitCount').innerText = d.count.toLocaleString())
        .catch(() => {});

        // Check for saved theme preference or default to dark
        const currentTheme = localStorage.getItem('theme') || 'dark';
        const html = document.documentElement;
        const themeIcon = document.getElementById('themeIcon');
        const themeText = document.getElementById('themeText');

        // Apply saved theme on load
        if (currentTheme === 'light') {
            html.classList.add('light-mode');
            themeIcon.textContent = '☀️';
            themeText.textContent = '라이트 모드';
        }

        function toggleTheme() {
            html.classList.toggle('light-mode');

            if (html.classList.contains('light-mode')) {
                themeIcon.textContent = '☀️';
                themeText.textContent = '라이트 모드';
                localStorage.setItem('theme', 'light');
            } else {
                themeIcon.textContent = '🌙';
                themeText.textContent = '다크 모드';
                localStorage.setItem('theme', 'dark');
            }
        }

        // Keyboard accessibility
        document.addEventListener('keydown', (e) => {
            if (e.key === 't' && e.ctrlKey) {
                e.preventDefault();
                toggleTheme();
            }
        });
