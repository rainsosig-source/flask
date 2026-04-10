import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, Response

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

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
from auth_blueprint import auth_bp
from route_blueprint import route_bp
from vuln_blueprint import vuln_bp
from client_api import client_bp

app.register_blueprint(podcast_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(route_bp)
app.register_blueprint(vuln_bp)
app.register_blueprint(client_bp, url_prefix="/api/client")


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


# === SEO Routes ===

@app.route('/robots.txt')
def robots_txt():
    txt = "User-agent: *\nAllow: /\nAllow: /podcast\nAllow: /broadcast\nAllow: /about\nDisallow: /manager\nDisallow: /api/\n\nSitemap: https://sosig.shop/sitemap.xml\n"
    return Response(txt, mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    pages = ['/', '/podcast', '/broadcast', '/about', '/route', '/vuln']
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for page in pages:
        xml += f'  <url><loc>https://sosig.shop{page}</loc></url>\n'
    xml += '</urlset>'
    return Response(xml, mimetype='application/xml')


if __name__ == '__main__':
    sys.stdout.reconfigure(line_buffering=True)
    app.run(host='0.0.0.0', port=5000)
