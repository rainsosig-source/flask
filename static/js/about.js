// Dark Mode Toggle
        const currentTheme = localStorage.getItem('theme') || 'dark';
        const html = document.documentElement;
        const themeIcon = document.getElementById('themeIcon');
        const themeText = document.getElementById('themeText');

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
                mermaid.initialize({ startOnLoad: true, theme: 'default' });
            } else {
                themeIcon.textContent = '🌙';
                themeText.textContent = '다크 모드';
                localStorage.setItem('theme', 'dark');
                mermaid.initialize({ startOnLoad: true, theme: 'dark' });
            }

            location.reload();
        }

        // Mermaid initialization
        document.addEventListener('DOMContentLoaded', function () {
            const isDark = !html.classList.contains('light-mode');
            mermaid.initialize({
                startOnLoad: true,
                theme: isDark ? 'dark' : 'default',
                securityLevel: 'loose',
            });
        });

        // Keyboard accessibility
        document.addEventListener('keydown', (e) => {
            if (e.key === 't' && e.ctrlKey) {
                e.preventDefault();
                toggleTheme();
            }
        });
