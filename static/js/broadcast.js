const API = '/api/guestbook';
let currentPage = 1;

async function loadMessages(page) {
    currentPage = page || 1;
    try {
        const res = await fetch(`${API}?page=${currentPage}`);
        const data = await res.json();
        renderMessages(data.messages);
        renderPagination(data.total, data.per_page);
        document.getElementById('totalCount').textContent = data.total;
    } catch (e) {
        console.error('Load error:', e);
    }
}

function renderMessages(messages) {
    const container = document.getElementById('messageList');
    if (!messages || messages.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">&#128236;</div>
                <p>아직 메시지가 없습니다. 첫 번째 방문자가 되어주세요!</p>
            </div>`;
        return;
    }
    container.innerHTML = messages.map(m => `
        <div class="msg-card">
            <div class="msg-top">
                <span class="msg-nickname">${escapeHtml(m.nickname)}</span>
                <span class="msg-date">${m.created_at}</span>
            </div>
            <div class="msg-body">${escapeHtml(m.message)}</div>
        </div>
    `).join('');
}

function renderPagination(total, perPage) {
    const pages = Math.ceil(total / perPage);
    const container = document.getElementById('pagination');
    if (pages <= 1) { container.innerHTML = ''; return; }

    let html = '';
    for (let i = 1; i <= pages; i++) {
        html += `<button class="page-btn${i === currentPage ? ' active' : ''}" onclick="loadMessages(${i})">${i}</button>`;
    }
    container.innerHTML = html;
}

async function submitMessage() {
    const nicknameEl = document.getElementById('nickname');
    const messageEl = document.getElementById('message');
    const btn = document.getElementById('submitBtn');
    const nickname = nicknameEl.value.trim();
    const message = messageEl.value.trim();

    if (!nickname || !message) {
        showToast('닉네임과 메시지를 모두 입력해주세요.', true);
        return;
    }

    btn.disabled = true;
    try {
        const res = await fetch(API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nickname, message })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('등록되었습니다!');
            messageEl.value = '';
            document.getElementById('charCount').textContent = '0';
            loadMessages(1);
        } else {
            showToast(data.error || '오류가 발생했습니다.', true);
        }
    } catch (e) {
        showToast('서버 오류가 발생했습니다.', true);
    } finally {
        btn.disabled = false;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(msg, isError) {
    let toast = document.querySelector('.toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.toggle('error', !!isError);
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

// Char counter
document.getElementById('message').addEventListener('input', function () {
    document.getElementById('charCount').textContent = this.value.length;
});

// Theme
const html = document.documentElement;
const saved = localStorage.getItem('theme') || 'dark';
if (saved === 'light') {
    html.classList.add('light-mode');
    document.getElementById('themeBtn').textContent = '\u2600\uFE0F';
}
function toggleTheme() {
    html.classList.toggle('light-mode');
    const isLight = html.classList.contains('light-mode');
    document.getElementById('themeBtn').textContent = isLight ? '\u2600\uFE0F' : '\uD83C\uDF19';
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
}

loadMessages(1);
