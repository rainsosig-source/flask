// Tab switching
        function switchTab(tabName) {
            // Hide all tabs
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });

            // Show selected tab
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');
        }

        // Copy code to clipboard
        function copyCode(button) {
            const codeBlock = button.closest('.code-container').querySelector('.code-block');
            const text = codeBlock.textContent;

            navigator.clipboard.writeText(text).then(() => {
                // Success feedback
                button.classList.add('copied');
                button.querySelector('.copy-icon').textContent = '✅';
                button.querySelector('.copy-text').textContent = '복사됨!';

                // Reset after 2 seconds
                setTimeout(() => {
                    button.classList.remove('copied');
                    button.querySelector('.copy-icon').textContent = '📋';
                    button.querySelector('.copy-text').textContent = '복사';
                }, 2000);
            }).catch(err => {
                console.error('Failed to copy:', err);
                alert('복사에 실패했습니다.');
            });
        }

        // Animate gauge on load
        document.addEventListener('DOMContentLoaded', () => {
            const needle = document.querySelector('.gauge-needle');
            if (needle) {
                needle.style.transition = 'none';
                needle.style.transform = 'rotate(-90deg)';
                setTimeout(() => {
                    needle.style.transition = 'transform 1s ease-out';
                    needle.style.transform = '';
                }, 100);
            }
        });
