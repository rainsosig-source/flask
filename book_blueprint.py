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

    # 시리즈(series_slug)로 묶어 대표 카드 1개로. 나머지는 개별 카드.
    cards, groups = [], {}
    for b in enriched:
        s = b.get("series_slug")
        if s:
            groups.setdefault(s, []).append(b)
        else:
            cards.append(b)
    for s, vols in groups.items():
        vols.sort(key=lambda x: x.get("slug", ""))
        rep = vols[0]
        cards.append({
            "is_series": True,
            "series_slug": s,
            "title": rep.get("series_title") or rep.get("title"),
            "subtitle": rep.get("series_subtitle") or rep.get("subtitle"),
            "author": rep.get("author"),
            "author_years": rep.get("author_years"),
            "summary": rep.get("series_summary") or rep.get("summary"),
            "cover_emoji": rep.get("cover_emoji"),
            "tags": rep.get("tags"),
            "published_at": max(v.get("published_at", "") for v in vols),
            "vol_count": len(vols),
            "pdf_file": True,
            "pdf_available": any(v.get("pdf_available") for v in vols),
        })

    cards.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return render_template("book_index.html", books=cards)


@book_bp.route("/series/<series_slug>")
def series(series_slug):
    books = _load_books()
    vols = [b for b in books if b.get("series_slug") == series_slug]
    if not vols:
        abort(404)
    vols.sort(key=lambda x: x.get("slug", ""))
    rep = vols[0]
    enriched = []
    for b in vols:
        available, size_mb = _pdf_info(b)
        enriched.append({**b, "pdf_available": available, "pdf_size_display": size_mb})
    return render_template(
        "book_series.html",
        series_title=rep.get("series_title") or rep.get("title"),
        series_subtitle=rep.get("series_subtitle") or rep.get("subtitle"),
        series_summary=rep.get("series_summary") or rep.get("summary"),
        author=rep.get("author"),
        author_years=rep.get("author_years"),
        cover_emoji=rep.get("cover_emoji"),
        volumes=enriched,
    )


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
