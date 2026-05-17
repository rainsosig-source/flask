"""sosig.shop/book — AI 번역 선집 소개 페이지.

- /book/          책 목록
- /book/<slug>    개별 책 상세 (소개 + 목차 + 다운로드)

데이터: /opt/flask-app/data/books.json
PDF:   /opt/flask-app/static/<pdf_file>
"""
import json
from pathlib import Path
from flask import Blueprint, render_template, abort

book_bp = Blueprint("book", __name__, url_prefix="/book")

BOOKS_JSON = Path("/opt/flask-app/data/books.json")
STATIC_DIR = Path("/opt/flask-app/static")


def _load_books():
    if not BOOKS_JSON.exists():
        return []
    try:
        return json.loads(BOOKS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def _pdf_info(book):
    pdf_file = book.get("pdf_file", "")
    if not pdf_file:
        return False, None
    pdf_path = STATIC_DIR / pdf_file
    if pdf_path.exists() and pdf_path.is_file():
        size_bytes = pdf_path.stat().st_size
        # 100KB 이상이면 MB 단위 소수점 표기, 미만이면 KB
        if size_bytes >= 100 * 1024:
            return True, f"{size_bytes / 1024 / 1024:.2f}MB"
        return True, f"{size_bytes // 1024}KB"
    return False, None


@book_bp.route("/", strict_slashes=False)
def index():
    books = _load_books()
    enriched = []
    for b in books:
        available, size_mb = _pdf_info(b)
        enriched.append({**b, "pdf_available": available, "pdf_size_display": size_mb})
    enriched.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return render_template("book_index.html", books=enriched)


@book_bp.route("/<slug>")
def detail(slug):
    books = _load_books()
    book = next((b for b in books if b["slug"] == slug), None)
    if not book:
        abort(404)
    available, size_mb = _pdf_info(book)

    # 목차를 부별로 그룹화
    parts = {}
    for ch in book.get("chapters", []):
        parts.setdefault(ch["part"], {
            "num": ch["part"], "title": ch["part_title"], "items": []
        })["items"].append(ch["ko_title"])
    parts_list = [parts[k] for k in sorted(parts)]

    return render_template(
        "book_detail.html",
        book=book,
        pdf_available=available,
        pdf_size_display=size_mb,
        parts=parts_list,
    )
