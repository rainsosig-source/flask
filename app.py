import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, Response

# systemd LoadCredentialEncrypted .env → os.environ (평문 EnvironmentFile 대체, override=평소 동작 복제)
_cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
if _cred_dir:
    _ef = os.path.join(_cred_dir, "envfile")
    if os.path.exists(_ef):
        with open(_ef, encoding="utf-8") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if _ln and not _ln.startswith("#") and "=" in _ln:
                    _k, _v = _ln.split("=", 1)
                    os.environ[_k.strip()] = _v.strip().strip(chr(34)).strip(chr(39))

app = Flask(__name__, static_folder='static', static_url_path='/static')
_secret = os.environ.get('FLASK_SECRET_KEY')
if not _secret:
    raise RuntimeError('FLASK_SECRET_KEY must be set in environment (no insecure default).')
app.secret_key = _secret
# 보안 하드닝 2026-05-23: 세션 쿠키 플래그
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=True,
)

# === Static asset cache-bust — 파일 mtime을 ?v= 쿼리로 자동 부착 ===
@app.context_processor
def _inject_static_v():
    def static_v(filename: str) -> str:
        try:
            mtime = int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            mtime = 0
        return f'/static/{filename}?v={mtime}'
    return dict(static_v=static_v)

# === Logging Setup ===
if not os.path.exists('/var/log/flask-app'):
    os.makedirs('/var/log/flask-app')

_file_handler = RotatingFileHandler(
    '/var/log/flask-app/app.log',
    maxBytes=10 * 1024 * 1024,
    backupCount=5
)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s [%(funcName)s] %(message)s'
))
_file_handler.setLevel(logging.INFO)
app.logger.addHandler(_file_handler)
app.logger.setLevel(logging.INFO)

# === Blueprints ===
from podcast_blueprint import podcast_bp
from subway_blueprint import subway_bp
from auth_blueprint import auth_bp
from route_blueprint import route_bp, limiter as route_limiter
from vuln_blueprint import vuln_bp
from malware_blueprint import malware_bp
from openvas_blueprint import openvas_bp
from security_blueprint import security_bp
from book_blueprint import book_bp
from video_blueprint import video_bp
from status_blueprint import status_bp
from kisa_blueprint import kisa_bp
from foreign_blueprint import foreign_bp

app.register_blueprint(podcast_bp)
app.register_blueprint(subway_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(route_bp)
app.register_blueprint(vuln_bp)
app.register_blueprint(malware_bp)
app.register_blueprint(openvas_bp, url_prefix="/vuln")
app.register_blueprint(security_bp)
app.register_blueprint(book_bp)
from network_blueprint import network_bp
app.register_blueprint(network_bp)
app.register_blueprint(video_bp)
app.register_blueprint(status_bp)
app.register_blueprint(kisa_bp)
app.register_blueprint(foreign_bp)

# /route/trace에 IP당 rate-limit (분 5건, 시간 30건)
route_limiter.init_app(app)


# === Pages ===

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')


# === Error Handlers ===

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404, message='Page Not Found'), 404

@app.errorhandler(500)
def internal_server_error(e):
    app.logger.error(f'500 error: {e}')
    return render_template('error.html', code=500, message='Internal Server Error'), 500


# === /api/v1/* 별칭 자동 등록 — 기존 /api/* 는 유지 ===
def _register_v1_aliases():
    seen = set()
    rules_snapshot = list(app.url_map.iter_rules())
    for rule in rules_snapshot:
        if not rule.rule.startswith('/api/') or rule.rule.startswith('/api/v1/'):
            continue
        alias = '/api/v1/' + rule.rule[len('/api/'):]
        if alias in seen or any(r.rule == alias for r in app.url_map.iter_rules()):
            continue
        seen.add(alias)
        methods = set(rule.methods or []) - {'HEAD', 'OPTIONS'}
        app.add_url_rule(
            alias,
            endpoint=rule.endpoint + '__v1',
            view_func=app.view_functions[rule.endpoint],
            methods=methods or None,
        )

_register_v1_aliases()


# === SEO Routes ===

@app.route('/robots.txt')
def robots_txt():
    txt = "User-agent: *\nAllow: /\nAllow: /podcast\nAllow: /broadcast\nAllow: /about\nDisallow: /manager\nDisallow: /api/\n\nSitemap: https://sosig.shop/sitemap.xml\n"
    return Response(txt, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    # (path, changefreq, priority)
    pages = [
        ('/',              'daily',   '1.0'),
        # 콘텐츠
        ('/podcast',       'hourly',  '0.9'),
        ('/briefing',      'daily',   '0.8'),
        ('/news/videos',   'daily',   '0.8'),
        ('/foreign',       'daily',   '0.8'),
        ('/book',          'monthly', '0.7'),
        # 보안
        ('/kisa',          'daily',   '0.8'),
        ('/vuln',          'daily',   '0.8'),
        ('/malware',       'monthly', '0.7'),
        ('/vuln/infra',    'weekly',  '0.6'),
        # 도구
        ('/route',         'monthly', '0.6'),
        ('/subway',        'weekly',  '0.6'),
        # 운영·메타
        ('/broadcast',     'weekly',  '0.5'),
        ('/about',         'monthly', '0.5'),
        ('/status',        'daily',   '0.4'),
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for path, changefreq, priority in pages:
        xml += (
            f'  <url><loc>https://sosig.shop{path}</loc>'
            f'<changefreq>{changefreq}</changefreq>'
            f'<priority>{priority}</priority></url>\n'
        )
    xml += '</urlset>'
    return Response(xml, mimetype='application/xml')


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    app.run(host='0.0.0.0', port=5000)
