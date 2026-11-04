"""Web scraping for the Genre Lens dashboard: critic review scores for recent films.

1. Scrapes Wikipedia's yearly "List of American films of YEAR" pages (HTML tables, parsed with
   BeautifulSoup) for every film's title and release date.
2. Looks each film up on Wikidata for its genres and its Rotten Tomatoes / Metacritic scores.
3. Converts each score to the same 0.5–5 scale as MovieLens ratings, so every score becomes one
   review row (score, date, genres) that can be added straight onto your MovieLens data.

Requests go through the public MediaWiki / Wikidata APIs with a descriptive User-Agent and a pause
between requests. Results are saved in their own SQLite file; your snapshots are never changed.
"""

from __future__ import annotations

import hashlib
import math
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as date_type, datetime
from pathlib import Path

import pandas as pd

try:  # scraping packages are only needed when the Scrape button is used
    import requests
    from bs4 import BeautifulSoup
    SCRAPING_AVAILABLE = True
except ImportError:  # app still opens; the sidebar explains how to install them
    requests = None
    BeautifulSoup = None
    SCRAPING_AVAILABLE = False

INSTALL_HINT = "python -m pip install requests beautifulsoup4"

ROOT = Path(__file__).resolve().parent
SCRAPE_DIR = ROOT / "databases" / "web_scrape"          # inside the project; ignored by git
SCRAPE_DB = SCRAPE_DIR / "critic_reviews.sqlite3"
LEGACY_SCRAPE_DB = ROOT.parent / "02_Databases" / "web_scrape" / "critic_reviews.sqlite3"   # older app versions

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "GenreLens/1.0 (PDS301m student project; educational film review analysis) python-requests"
REQUEST_PAUSE_SECONDS = 0.35
FIRST_YEAR = 2020

# Critic sites on Wikidata (qualifier P447 "review score by") -> how they appear as reviewers in the data.
CRITIC_SOURCES = {"Q105584": ("Rotten Tomatoes critics", -1), "Q150248": ("Metacritic critics", -2)}

MONTHS = {name: index for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}
MONTHS.update({name[:3]: index for name, index in list(MONTHS.items())})
MONTHS["sept"] = 9

# Keywords in Wikidata genre labels -> MovieLens genres.
GENRE_KEYWORDS = [
    (r"\bromanc|\bromantic", ["Romance"]),
    (r"\bcomed|\bsitcom|\bparody|\bsatir", ["Comedy"]),
    (r"\baction\b|\bmartial arts|\bdisaster|\bheist", ["Action"]),
    (r"\badventure|\bsword and sorcery|\bswashbuckl", ["Adventure"]),
    (r"\bsuperhero", ["Action", "Adventure", "Sci-Fi"]),
    (r"\banimat|\banime\b|\bcomputer-animated|\bstop-motion", ["Animation"]),
    (r"\bchildren|\bfamily film|\bfamily\b", ["Children"]),
    (r"\bcrime|\bgangster|\bmafia|\bheist|\bpolice", ["Crime"]),
    (r"\bdocumentar", ["Documentary"]),
    (r"\bdrama|\bbiographical|\bbiopic|\bhistorical|\bsports film|\bcoming-of-age|\bperiod", ["Drama"]),
    (r"\bfantasy|\bfairy tale|\bsupernatural", ["Fantasy"]),
    (r"\bnoir\b", ["Film-Noir"]),
    (r"\bhorror|\bslasher|\bzombie|\bmonster film|\bgore", ["Horror"]),
    (r"\bmusical|\bmusic film|\bdance film|\bconcert film", ["Musical"]),
    (r"\bmyster|\bdetective|\bwhodunit", ["Mystery"]),
    (r"\bscience fiction|\bsci-fi|\bcyberpunk|\bspace opera|\bdystopi|\bkaiju|\bapocalyptic", ["Sci-Fi"]),
    (r"\bthriller|\bsuspense|\bspy\b|\bespionage|\bpsychological", ["Thriller"]),
    (r"\bwar film|\bwar drama|\bwar\b|\bmilitary", ["War"]),
    (r"\bwestern", ["Western"]),
]


ARTICLES = {"the", "a", "an", "les", "la", "le", "l'", "il", "die", "der", "das", "el", "los", "las", "lo", "i", "gli"}


# --------------------------------------------------------------------------- HTTP

class _Client:
    def __init__(self, pause: float = REQUEST_PAUSE_SECONDS):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        self.pause = pause
        self._last = 0.0

    def get_json(self, url: str, params: dict) -> dict:
        params = {**params, "format": "json", "formatversion": 2}
        for attempt in range(4):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=25)
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(float(response.headers.get("Retry-After", 2 ** (attempt + 1))))
                continue
            response.raise_for_status()
            data = response.json()
            if data.get("error", {}).get("code") == "maxlag":
                time.sleep(5)
                continue
            return data
        raise RuntimeError(f"Gave up after repeated errors from {url}")


def _clean(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text or "")          # footnote markers like [1] or [a]
    text = text.replace("\xa0", " ").replace("†", "").replace("‡", "").replace("*", "")
    return re.sub(r"\s+", " ", text).strip()


def _expand_table(table) -> list[list]:
    """Turn an HTML table into a grid of cells with rowspan/colspan expanded."""
    grid: list[list] = []
    pending: dict[int, list] = {}  # column -> [cell, rows_left]
    for tr in table.find_all("tr"):
        row: list = []
        cells = tr.find_all(["td", "th"], recursive=False)
        column = 0
        cell_iter = iter(cells)
        while True:
            if column in pending:
                cell, rows_left = pending[column]
                row.append(cell)
                if rows_left <= 1:
                    del pending[column]
                else:
                    pending[column][1] = rows_left - 1
                column += 1
                continue
            cell = next(cell_iter, None)
            if cell is None:
                break
            try:
                rowspan = max(1, int(re.sub(r"\D", "", cell.get("rowspan", "1")) or 1))
                colspan = max(1, int(re.sub(r"\D", "", cell.get("colspan", "1")) or 1))
            except ValueError:
                rowspan = colspan = 1
            for _ in range(colspan):
                row.append(cell)
                if rowspan > 1:
                    pending[column] = [cell, rowspan - 1]
                column += 1
        # any remaining spanned columns past the last real cell
        while column in pending:
            cell, rows_left = pending[column]
            row.append(cell)
            if rows_left <= 1:
                del pending[column]
            else:
                pending[column][1] = rows_left - 1
            column += 1
        if row:
            grid.append(row)
    return grid


def map_genre_labels(labels: list[str]) -> list[str]:
    genres: list[str] = []
    for label in labels:
        lowered = label.lower()
        for pattern, targets in GENRE_KEYWORDS:
            if re.search(pattern, lowered):
                for target in targets:
                    if target not in genres:
                        genres.append(target)
    return genres




# --------------------------------------------------------------------------- storage

def _connect() -> sqlite3.Connection:
    SCRAPE_DIR.mkdir(parents=True, exist_ok=True)
    if not SCRAPE_DB.exists() and LEGACY_SCRAPE_DB.exists():
        shutil.copyfile(LEGACY_SCRAPE_DB, SCRAPE_DB)       # keep reviews scraped by an older version
    connection = sqlite3.connect(SCRAPE_DB)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS films (
            wiki_title TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            release_date TEXT NOT NULL,
            year INTEGER NOT NULL,
            wikidata_id TEXT,
            wikidata_genres TEXT,
            mapped_genres TEXT,
            rt_raw TEXT,
            rt_score REAL,
            mc_raw TEXT,
            mc_score REAL,
            looked_up TEXT
        );
        CREATE TABLE IF NOT EXISTS scrape_log (
            year INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            films INTEGER NOT NULL,
            scraped_at TEXT NOT NULL
        );
    """)
    return connection


def scrape_status() -> dict[str, object]:
    """Summary of the saved scrape (safe to call before anything was scraped)."""
    empty = {"films": 0, "reviews": 0, "pending": 0, "first": None, "last": None, "scraped_at": None}
    if not SCRAPE_DB.exists() and not LEGACY_SCRAPE_DB.exists():
        return empty
    with _connect() as connection:
        films, pending, first, last = connection.execute(
            "SELECT COUNT(*), SUM(looked_up IS NULL AND release_date <= ?), MIN(release_date), MAX(release_date) FROM films",
            (date_type.today().isoformat(),)).fetchone()
        reviews = connection.execute("SELECT COUNT(rt_score) + COUNT(mc_score) FROM films").fetchone()[0]
        scraped_at = connection.execute("SELECT MAX(scraped_at) FROM scrape_log").fetchone()[0]
    return {"films": films or 0, "reviews": reviews or 0, "pending": pending or 0, "first": first, "last": last,
            "scraped_at": scraped_at}


def coverage_table() -> pd.DataFrame:
    """Films found, looked up and reviewed, per release year."""
    columns = ["Year", "Films found", "Looked up", "With critic score"]
    if not SCRAPE_DB.exists() and not LEGACY_SCRAPE_DB.exists():
        return pd.DataFrame(columns=columns)
    with _connect() as connection:
        table = pd.read_sql_query("""
            SELECT year, COUNT(*), SUM(looked_up IS NOT NULL), SUM(rt_score IS NOT NULL OR mc_score IS NOT NULL)
            FROM films GROUP BY year ORDER BY year DESC
        """, connection)
    table.columns = columns
    return table


# --------------------------------------------------------------------------- parsing

def _cell_text(cell) -> str:
    return _clean(cell.get_text(" "))


def _month_of(text: str) -> int | None:
    letters = re.sub(r"[^a-z]", "", text.lower())        # "J A N U A R Y" -> "january"
    for name, number in MONTHS.items():
        if letters.startswith(name):
            return number
    return None


def parse_film_list_page(html: str, year: int) -> list[dict]:
    """Films (title, link, release date) from a "List of American films of YEAR" page.

    The release tables start with an "Opening" header spanning two columns: the month (one tall
    merged cell per month) and the day (merged for films opening the same day).
    """
    soup = BeautifulSoup(html, "html.parser")
    films: dict[str, dict] = {}
    for table in soup.select("table.wikitable"):
        grid = _expand_table(table)
        if len(grid) < 2:
            continue
        header_row = next((row for row in grid if all(cell.name == "th" for cell in row)), grid[0])
        headers = [_cell_text(cell).lower() for cell in header_row]
        title_col = next((i for i, h in enumerate(headers) if h.startswith("title") or h == "film"), None)
        opening = [i for i, h in enumerate(headers) if "opening" in h or "release" in h]
        if title_col is None or not opening:
            continue
        month_col = opening[0]
        day_col = opening[1] if len(opening) > 1 else None
        last_month = None
        for row in grid:
            if row is header_row or len(row) <= title_col or all(cell.name == "th" for cell in row):
                continue
            month = _month_of(_cell_text(row[month_col])) or last_month
            last_month = month
            day_text = _cell_text(row[day_col]) if day_col is not None else _cell_text(row[month_col])
            day_match = re.search(r"\b(\d{1,2})\b", day_text)
            if not month or not day_match:
                continue
            try:
                released = date_type(year, month, int(day_match.group(1)))
            except ValueError:
                continue
            title_cell = row[title_col]
            title = _cell_text(title_cell)
            link = next((a for a in title_cell.find_all("a", href=True)
                         if a["href"].startswith("/wiki/") and ":" not in a["href"][6:]), None)
            if not title or not link:
                continue                       # films without an article cannot be looked up
            films.setdefault(link.get("title"), {"wiki_title": link.get("title"), "title": title,
                                                 "release_date": released.isoformat(), "year": year})
    return list(films.values())


def score_to_five(value: str) -> float | None:
    """'93%' -> 4.5, '85/100' -> 4.5, '7.4/10' -> 3.5 (rounded to MovieLens' 0.5 steps)."""
    text = str(value).strip().replace(",", ".")
    match = re.match(r"^(\d+(?:\.\d+)?)\s*(%|/\s*(\d+(?:\.\d+)?))?$", text)
    if not match:
        return None
    number = float(match.group(1))
    if match.group(2) == "%":
        scale = 100.0
    elif match.group(3):
        scale = float(match.group(3))
    else:
        scale = 100.0                          # Metacritic sometimes stores just "85"
    if scale <= 0 or number > scale:
        return None
    five = math.floor(number / scale * 5 * 2 + 0.5) / 2     # round half up to the nearest 0.5
    return min(5.0, max(0.5, five))


def _pick_scores(claims: list[dict]) -> dict[str, str]:
    """Latest Tomatometer (%) and Metascore (/100) per critic site from Wikidata P444 claims."""
    picked: dict[str, str] = {}
    for claim in claims:
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
        sources = [q.get("datavalue", {}).get("value", {}).get("id")
                   for q in claim.get("qualifiers", {}).get("P447", [])]
        for source in sources:
            if source not in CRITIC_SOURCES or not isinstance(value, str):
                continue
            wanted = value.strip().endswith("%") if source == "Q105584" else not value.strip().endswith("%") and "/10" not in value.replace("/100", "")
            if wanted:
                picked[source] = value.strip()     # later claims are newer, so they win
    return picked


# --------------------------------------------------------------------------- scraping

def _page_title(year: int) -> str:
    return f"List of American films of {year}"


def _fetch_page_html(client: _Client, title: str) -> str | None:
    data = client.get_json(WIKIPEDIA_API, {"action": "parse", "page": title, "prop": "text", "redirects": 1})
    if "error" in data:
        return None
    return data.get("parse", {}).get("text")


def _existing_pages(client: _Client, titles: list[str]) -> dict[str, str | None]:
    """Check up to 50 titles per request; returns title -> resolved page title, or None if missing."""
    found: dict[str, str | None] = {}
    for start in range(0, len(titles), 50):
        batch = titles[start:start + 50]
        data = client.get_json(WIKIPEDIA_API, {"action": "query", "titles": "|".join(batch), "prop": "info", "redirects": 1})
        query = data.get("query", {})
        rename = {item["from"]: item["to"] for item in query.get("normalized", []) + query.get("redirects", [])}
        exists = {page.get("title"): not page.get("missing", False) and "invalid" not in page for page in query.get("pages", [])}
        for title in batch:
            resolved, hops = title, 0
            while resolved in rename and hops < 5:
                resolved, hops = rename[resolved], hops + 1
            found[title] = resolved if exists.get(resolved, False) else None
    return found


def _lookup_films(client: _Client, connection: sqlite3.Connection, max_films: int, progress=None) -> dict[str, int]:
    """Genres + critic scores from Wikidata for the newest films not looked up yet (at most max_films)."""
    # Share the limit across the years: the newest film of every year first, then the next newest, ...
    # Films not released yet are skipped (no reviews exist yet); they are checked once they come out.
    titles = [row[0] for row in connection.execute("""
        SELECT wiki_title FROM (
            SELECT wiki_title, year, release_date,
                   ROW_NUMBER() OVER (PARTITION BY year ORDER BY release_date DESC) AS turn
            FROM films WHERE looked_up IS NULL AND release_date <= ?
        ) ORDER BY turn, year DESC LIMIT ?
    """, (date_type.today().isoformat(), max_films))]
    done = {"looked_up": 0, "with_scores": 0}
    for start in range(0, len(titles), 50):
        batch = titles[start:start + 50]
        if progress:
            progress(f"Reading genres and critic scores: films {start + 1:,}–{start + len(batch):,} of {len(titles):,}",
                     0.2 + 0.8 * start / max(len(titles), 1))
        data = client.get_json(WIKIPEDIA_API, {"action": "query", "titles": "|".join(batch),
                                               "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1})
        query = data.get("query", {})
        rename = {item["from"]: item["to"] for item in query.get("normalized", []) + query.get("redirects", [])}
        page_qids = {page.get("title"): page.get("pageprops", {}).get("wikibase_item") for page in query.get("pages", [])}
        qids = {}
        for title in batch:
            resolved, hops = title, 0
            while resolved in rename and hops < 5:
                resolved, hops = rename[resolved], hops + 1
            qids[title] = page_qids.get(resolved)
        wanted = sorted({qid for qid in qids.values() if qid})
        claims: dict[str, dict] = {}
        if wanted:
            entity_data = client.get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(wanted), "props": "claims"})
            claims = {qid: entity.get("claims", {}) for qid, entity in entity_data.get("entities", {}).items()}
        genre_ids = {qid: [c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                           for c in entity_claims.get("P136", [])] for qid, entity_claims in claims.items()}
        all_genre_ids = sorted({gid for ids in genre_ids.values() for gid in ids if gid})
        labels: dict[str, str] = {}
        for label_start in range(0, len(all_genre_ids), 50):
            label_data = client.get_json(WIKIDATA_API, {"action": "wbgetentities", "props": "labels", "languages": "en",
                                                        "ids": "|".join(all_genre_ids[label_start:label_start + 50])})
            for gid, entity in label_data.get("entities", {}).items():
                label = entity.get("labels", {}).get("en", {}).get("value")
                if label:
                    labels[gid] = label
        now = datetime.now().isoformat(timespec="seconds")
        for title in batch:
            qid = qids.get(title)
            entity_claims = claims.get(qid, {}) if qid else {}
            genre_labels = [labels[gid] for gid in genre_ids.get(qid, []) if gid in labels]
            scores = _pick_scores(entity_claims.get("P444", []))
            rt_raw, mc_raw = scores.get("Q105584"), scores.get("Q150248")
            rt, mc = (score_to_five(rt_raw) if rt_raw else None), (score_to_five(mc_raw) if mc_raw else None)
            connection.execute("""
                UPDATE films SET wikidata_id = ?, wikidata_genres = ?, mapped_genres = ?, rt_raw = ?, rt_score = ?,
                                 mc_raw = ?, mc_score = ?, looked_up = ? WHERE wiki_title = ?
            """, (qid, "|".join(genre_labels), "|".join(map_genre_labels(genre_labels)), rt_raw, rt, mc_raw, mc, now, title))
            done["looked_up"] += 1
            done["with_scores"] += int(rt is not None or mc is not None)
        connection.commit()                    # saved per batch, so Cancel keeps finished work
    return done


def scrape_reviews(first_year: int, last_year: int, max_films: int = 300, refresh_existing: bool = False,
                   progress=None, workers: int = 3) -> dict[str, int]:
    """Scrape film lists for the years (newest first), then look up at most ``max_films`` new films.

    Years already saved are skipped unless ``refresh_existing`` is set; the two most recent years
    are always re-read so newly released films appear. Films looked up before are never repeated.
    """
    if not SCRAPING_AVAILABLE:
        raise RuntimeError(f"Scraping needs two extra packages. Stop the app, run `{INSTALL_HINT}` in the same terminal, then start it again")
    client = _Client(pause=0.1)
    connection = _connect()
    current_year = date_type.today().year
    summary = {"pages": 0, "missing": 0, "errors": 0, "new_films": 0, "skipped": 0, "looked_up": 0, "with_scores": 0, "remaining": 0}
    executor = None
    try:
        done = {year for (year,) in connection.execute("SELECT year FROM scrape_log WHERE status = 'ok'")}
        years = [year for year in range(last_year, first_year - 1, -1)
                 if refresh_existing or year not in done or year >= current_year - 1]
        summary["skipped"] = (last_year - first_year + 1) - len(years)
        if progress:
            progress(f"Checking film list pages for {len(years)} year(s)…", 0.02)
        existing = _existing_pages(client, [_page_title(year) for year in years]) if years else {}
        local = threading.local()

        def fetch(year):
            if not hasattr(local, "client"):
                local.client = _Client(pause=0.1)
            return year, parse_film_list_page(_fetch_page_html(local.client, existing[_page_title(year)]) or "", year)

        now = datetime.now().isoformat(timespec="seconds")
        to_fetch = []
        for year in years:
            if existing.get(_page_title(year)):
                to_fetch.append(year)
            else:
                summary["missing"] += 1
                connection.execute("INSERT OR REPLACE INTO scrape_log VALUES (?, 'missing', 0, ?)", (year, now))
        connection.commit()
        executor = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = [executor.submit(fetch, year) for year in to_fetch]
        for finished, future in enumerate(as_completed(futures), start=1):
            try:
                year, films = future.result()
            except Exception:
                summary["errors"] += 1
                continue
            before = connection.total_changes
            connection.executemany("""
                INSERT INTO films (wiki_title, title, release_date, year) VALUES (:wiki_title, :title, :release_date, :year)
                ON CONFLICT(wiki_title) DO UPDATE SET title = excluded.title, release_date = excluded.release_date, year = excluded.year
            """, films)
            summary["new_films"] += connection.total_changes - before
            connection.execute("INSERT OR REPLACE INTO scrape_log VALUES (?, ?, ?, ?)",
                               (year, "ok" if films else "empty", len(films), datetime.now().isoformat(timespec="seconds")))
            connection.commit()
            summary["pages"] += 1
            if progress:
                progress(f"Film lists: {finished}/{len(to_fetch)} pages · {year}: {len(films):,} films", 0.05 + 0.15 * finished / max(len(to_fetch), 1))
        executor.shutdown(wait=True)
        executor = None
        lookup = _lookup_films(client, connection, max_films, progress)
        summary.update(lookup)
        summary["remaining"] = connection.execute("SELECT COUNT(*) FROM films WHERE looked_up IS NULL AND release_date <= ?",
                                                  (date_type.today().isoformat(),)).fetchone()[0]
    finally:
        if executor is not None:   # stopped early (Cancel): drop queued downloads, keep what is saved
            executor.shutdown(wait=False, cancel_futures=True)
        connection.close()
    return summary


# --------------------------------------------------------------------------- matching to MovieLens

def normalize_title(title: str) -> str:
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    title = re.sub(r"\s*\((?:\d{4}|film|\d{4} film|[a-z ]*film)\)\s*$", "", title)   # "(2010 film)", "(film)"
    title = title.replace("&", " and ")
    title = re.sub(r"[^a-z0-9 ]+", " ", title)
    words = title.split()
    if words and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def movielens_title_variants(title: str) -> tuple[list[str], int | None]:
    """'Matrix, The (1999)' -> (['matrix'], 1999); also indexes alternate titles in parentheses."""
    year_match = re.search(r"\((\d{4})\)\s*$", title.strip())
    year = int(year_match.group(1)) if year_match else None
    base = re.sub(r"\s*\(\d{4}\)\s*$", "", title.strip())
    parts = [re.sub(r"\s*\([^)]*\)", "", base)] + re.findall(r"\(([^)]*)\)", base)
    variants = []
    for part in parts:
        part = part.strip()
        article = re.match(r"^(.*),\s*(the|a|an|les|la|le|l'|il|die|der|das|el|los|las)$", part, flags=re.IGNORECASE)
        if article:
            part = f"{article.group(2)} {article.group(1)}"
        normalized = normalize_title(part)
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants, year


def build_movielens_index(movies: pd.DataFrame) -> dict[str, list[tuple[int | None, int, str, str]]]:
    """Normalized title -> [(year, movie_id, title, genres)] for every film in a MovieLens catalog."""
    index: dict[str, list[tuple[int | None, int, str, str]]] = {}
    for movie_id, title, genres in zip(movies["movie_id"], movies["title"], movies["genres"]):
        variants, year = movielens_title_variants(title)
        for variant in variants:
            index.setdefault(variant, []).append((year, int(movie_id), title, genres))
    return index


def _match(index, title: str, wiki_title: str, year: int):
    for candidate in {normalize_title(title), normalize_title(wiki_title)} - {""}:
        for option_year, movie_id, ml_title, genres in index.get(candidate, []):
            if option_year is None or abs(option_year - year) <= 1:
                return movie_id, ml_title, genres
    return None


# --------------------------------------------------------------------------- data for the dashboard

def build_review_view(movielens_db: str, include_mine: bool) -> str | None:
    """A database in the MovieLens snapshot format holding the scraped critic reviews.

    include_mine=False -> only the scraped reviews ("Scraped reviews").
    include_mine=True  -> a copy of your snapshot with the scraped reviews added ("Both").

    Every critic score becomes one row of ``ratings``: the critic site is the reviewer (negative ids,
    so they never clash with MovieLens users), the score is on the same 0.5–5 scale and the date is
    the film's release date. Films already in your catalog keep their MovieLens id and genres, so the
    two datasets add together cleanly; new films get new ids and their Wikidata genres.
    """
    if not SCRAPE_DB.exists():
        return None
    from data_store import movie_catalog

    snapshot = Path(movielens_db)
    key = f"{snapshot}|{snapshot.stat().st_mtime}|{include_mine}|{SCRAPE_DB.stat().st_mtime}|v2"
    path = SCRAPE_DIR / f"review_view_{hashlib.md5(key.encode()).hexdigest()[:12]}.sqlite3"
    if path.exists():
        return str(path)

    with _connect() as connection:
        films = pd.read_sql_query("SELECT * FROM films WHERE rt_score IS NOT NULL OR mc_score IS NOT NULL", connection)
    if films.empty:
        return None
    catalog = movie_catalog(movielens_db)
    index = build_movielens_index(catalog)
    next_id = int(catalog["movie_id"].max() or 0) + 1

    movie_rows, genre_rows, rating_rows, used_ids = [], [], [], set()
    for film in films.itertuples(index=False):
        release_year = int(film.release_date[:4])
        matched = _match(index, film.title, film.wiki_title, release_year)
        if matched:
            movie_id, ml_title, ml_genres = matched
            genres = [] if ml_genres == "(no genres listed)" else ml_genres.split("|")
            title = ml_title
        else:
            movie_id, next_id = next_id, next_id + 1
            genres = [genre for genre in (film.mapped_genres or "").split("|") if genre]
            title = f"{film.title} ({release_year})"
        if movie_id not in used_ids and not (matched and include_mine):
            movie_rows.append((movie_id, title, release_year))
            genre_rows.extend((movie_id, genre, release_year) for genre in genres)
        used_ids.add(movie_id)
        timestamp = int(pd.Timestamp(film.release_date).tz_localize("UTC").timestamp()) + 12 * 3600
        for score, (_, critic_id) in ((film.rt_score, CRITIC_SOURCES["Q105584"]), (film.mc_score, CRITIC_SOURCES["Q150248"])):
            if score is not None and not pd.isna(score):
                rating_rows.append((critic_id, movie_id, float(score), timestamp))

    building = Path(str(path) + ".building")
    building.unlink(missing_ok=True)
    if include_mine:
        shutil.copyfile(movielens_db, building)
    connection = sqlite3.connect(building)
    try:
        if not include_mine:
            connection.executescript("""
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE movies (movie_id INTEGER PRIMARY KEY, title TEXT NOT NULL, release_year INTEGER);
                CREATE TABLE movie_genres (movie_id INTEGER NOT NULL, genre TEXT NOT NULL, release_year INTEGER,
                                           PRIMARY KEY (movie_id, genre));
                CREATE TABLE ratings (user_id INTEGER NOT NULL, movie_id INTEGER NOT NULL, rating REAL NOT NULL, timestamp INTEGER NOT NULL);
            """)
        connection.executemany("INSERT OR IGNORE INTO movies VALUES (?, ?, ?)", movie_rows)
        connection.executemany("INSERT OR IGNORE INTO movie_genres VALUES (?, ?, ?)", genre_rows)
        connection.executemany("INSERT INTO ratings VALUES (?, ?, ?, ?)", rating_rows)
        if not include_mine:
            connection.executescript("""
                CREATE INDEX ratings_timestamp_idx ON ratings(timestamp);
                CREATE INDEX ratings_movie_idx ON ratings(movie_id);
                CREATE INDEX movie_genres_genre_idx ON movie_genres(genre);
            """)
        base_name = dict(connection.execute("SELECT key, value FROM metadata")).get("name", "Your data") if include_mine else ""
        connection.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", {
            "name": f"{base_name} + scraped reviews" if include_mine else "Scraped critic reviews",
            "source": "Wikipedia film lists + Wikidata critic scores",
            "imported": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            "scraped_reviews": str(len(rating_rows)),
        }.items())
        connection.commit()
    finally:
        connection.close()
    building.replace(path)
    for old in sorted(SCRAPE_DIR.glob("review_view_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)[6:]:
        old.unlink(missing_ok=True)
    return str(path)
