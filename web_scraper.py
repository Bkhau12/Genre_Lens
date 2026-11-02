"""Web scraping for the Genre Lens dashboard.

Collects weekend box office number-one films from Wikipedia for many countries,
then attaches genres to every film:

1. If the film is in the active MovieLens catalog (title + year match), its
   MovieLens genres are used, so scraped data and ratings share one genre list.
2. Otherwise the film's genres are read from Wikidata and mapped onto the same
   MovieLens genre names (Action, Comedy, Romance, ...).

Pages are fetched through the public MediaWiki API with a descriptive
User-Agent and a pause between requests, following Wikipedia's bot etiquette.
Everything is stored in a separate SQLite file so imports of MovieLens
snapshots are never touched.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import unicodedata
from datetime import date as date_type, datetime, timedelta
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
SCRAPE_DIR = ROOT.parent / "02_Databases" / "web_scrape"
SCRAPE_DB = SCRAPE_DIR / "box_office_weekends.sqlite3"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = "GenreLens/1.0 (PDS301m student project; educational box office analysis) python-requests"
REQUEST_PAUSE_SECONDS = 0.35

# Display name -> how Wikipedia names the country in "List of 2024 box office number-one films in ___".
COUNTRIES = {
    "United States": "the United States",
    "United Kingdom": "the United Kingdom",
    "France": "France",
    "Germany": "Germany",
    "Italy": "Italy",
    "Spain": "Spain",
    "Russia": "Russia",
    "Japan": "Japan",
    "South Korea": "South Korea",
    "China": "China",
    "Hong Kong": "Hong Kong",
    "Taiwan": "Taiwan",
    "Thailand": "Thailand",
    "Australia": "Australia",
    "New Zealand": "New Zealand",
    "Mexico": "Mexico",
    "Brazil": "Brazil",
    "Argentina": "Argentina",
}

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


# --------------------------------------------------------------------------- storage

def _connect() -> sqlite3.Connection:
    SCRAPE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(SCRAPE_DB)
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS weekends (
            country TEXT NOT NULL,
            weekend_date TEXT NOT NULL,
            film_title TEXT NOT NULL,
            wiki_title TEXT,
            gross_text TEXT,
            gross_value REAL,
            source_page TEXT NOT NULL,
            PRIMARY KEY (country, weekend_date)
        );
        CREATE TABLE IF NOT EXISTS films (
            wiki_title TEXT PRIMARY KEY,
            wikidata_id TEXT,
            release_date TEXT,
            wikidata_genres TEXT,
            mapped_genres TEXT,
            looked_up TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scrape_log (
            country TEXT NOT NULL,
            year INTEGER NOT NULL,
            status TEXT NOT NULL,
            rows INTEGER NOT NULL,
            scraped_at TEXT NOT NULL,
            PRIMARY KEY (country, year)
        );
        CREATE INDEX IF NOT EXISTS weekends_date_idx ON weekends(weekend_date);
    """)
    return connection


def scrape_status() -> dict[str, object]:
    """Summary of what has been scraped so far (safe to call when nothing exists yet)."""
    if not SCRAPE_DB.exists():
        return {"weekends": 0, "films": 0, "countries": 0, "first": None, "last": None, "scraped_at": None}
    with _connect() as connection:
        weekends, countries, first, last = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT country), MIN(weekend_date), MAX(weekend_date) FROM weekends").fetchone()
        films = connection.execute("SELECT COUNT(DISTINCT COALESCE(wiki_title, film_title)) FROM weekends").fetchone()[0]
        scraped_at = connection.execute("SELECT MAX(scraped_at) FROM scrape_log").fetchone()[0]
    return {"weekends": weekends, "films": films, "countries": countries, "first": first, "last": last,
            "scraped_at": scraped_at}


def coverage_table() -> pd.DataFrame:
    if not SCRAPE_DB.exists():
        return pd.DataFrame(columns=["Country", "Years found", "Years missing", "#1 weekends"])
    with _connect() as connection:
        log = pd.read_sql_query("SELECT country, year, status, rows FROM scrape_log", connection)
    if log.empty:
        return pd.DataFrame(columns=["Country", "Years found", "Years missing", "#1 weekends"])
    table = log.groupby("country").agg(
        found=("status", lambda values: int((values == "ok").sum())),
        missing=("status", lambda values: int((values != "ok").sum())),
        weekends=("rows", "sum"),
    ).reset_index()
    table.columns = ["Country", "Years found", "Years missing", "#1 weekends"]
    return table.sort_values("#1 weekends", ascending=False)


def load_box_office() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (weekends, films) data frames from the scrape database."""
    if not SCRAPE_DB.exists():
        return pd.DataFrame(), pd.DataFrame()
    with _connect() as connection:
        weekends = pd.read_sql_query("SELECT * FROM weekends", connection)
        films = pd.read_sql_query("SELECT * FROM films", connection)
    if not weekends.empty:
        weekends["weekend_date"] = pd.to_datetime(weekends["weekend_date"])
    return weekends, films


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


# --------------------------------------------------------------------------- parsing helpers

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


def parse_weekend_date(text: str, page_year: int) -> date_type | None:
    """Parse 'January 7, 2024', '7 January 2024', 'January 5–7', '29 Dec – 1 Jan 2024' -> last day."""
    text = _clean(text).lower().replace(",", " ")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        try:
            return date_type(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    tokens = re.findall(r"[a-z]+|\d+", text)
    month = day = year = None
    for token in tokens:
        if token.isdigit():
            value = int(token)
            if len(token) == 4 and 1900 <= value <= 2100:
                year = value
            elif 1 <= value <= 31:
                day = value
        elif token in MONTHS:
            month = MONTHS[token]
    if month is None or day is None:
        return None
    year = year or page_year
    try:
        return date_type(year, month, day)
    except ValueError:
        return None


def _parse_gross(text: str) -> float | None:
    text = _clean(text).lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(billion|million|bn|m)?", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit in ("billion", "bn"):
        value *= 1e9
    elif unit in ("million", "m"):
        value *= 1e6
    return value


def _header_index(headers: list[str], *keywords: str) -> int | None:
    for keyword in keywords:
        for index, header in enumerate(headers):
            if keyword in header:
                return index
    return None


def parse_number_one_page(html: str, page_year: int) -> list[dict]:
    """Extract weekend rows (date, film, link title, gross) from a box office number-one list page."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for table in soup.select("table.wikitable"):
        grid = _expand_table(table)
        if len(grid) < 2:
            continue
        header_row = next((row for row in grid if all(cell.name == "th" for cell in row)), grid[0])
        headers = [_clean(cell.get_text(" ")).lower() for cell in header_row]
        film_col = _header_index(headers, "film", "title", "movie")
        date_col = _header_index(headers, "weekend end", "end date", "date", "weekend", "week")
        if film_col is None or date_col is None or film_col == date_col:
            continue
        gross_col = _header_index(headers, "weekend gross", "gross", "box office", "admissions", "revenue")
        for row in grid:
            if row is header_row or len(row) <= max(film_col, date_col):
                continue
            if all(cell.name == "th" for cell in row):
                continue
            weekend = parse_weekend_date(row[date_col].get_text(" "), page_year)
            if weekend is None or abs(weekend.year - page_year) > 1:
                continue
            film_cell = row[film_col]
            film_title = _clean(film_cell.get_text(" "))
            if not film_title or film_title.lower() in {"n/a", "—", "-"}:
                continue
            link = next((anchor for anchor in film_cell.find_all("a", href=True)
                         if anchor["href"].startswith("/wiki/") and ":" not in anchor["href"][6:]), None)
            wiki_title = link.get("title") if link else None
            gross_text = _clean(row[gross_col].get_text(" ")) if gross_col is not None and gross_col < len(row) else ""
            rows.append({
                "weekend_date": weekend.isoformat(),
                "film_title": film_title,
                "wiki_title": wiki_title,
                "gross_text": gross_text,
                "gross_value": _parse_gross(gross_text) if gross_text else None,
            })
    # one row per weekend (some pages repeat tables); keep the first occurrence
    unique = {}
    for row in rows:
        unique.setdefault(row["weekend_date"], row)
    return list(unique.values())


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


# --------------------------------------------------------------------------- scraping

def _page_title(country_key: str, year: int) -> str:
    return f"List of {year} box office number-one films in {COUNTRIES[country_key]}"


def _fetch_page_html(client: _Client, title: str) -> str | None:
    data = client.get_json(WIKIPEDIA_API, {"action": "parse", "page": title, "prop": "text", "redirects": 1})
    if "error" in data:
        return None
    return data.get("parse", {}).get("text")


def _lookup_film_genres(client: _Client, connection: sqlite3.Connection, progress=None) -> int:
    """Look up Wikidata genres for linked films that have not been looked up yet."""
    titles = [row[0] for row in connection.execute("""
        SELECT DISTINCT w.wiki_title FROM weekends w
        LEFT JOIN films f ON f.wiki_title = w.wiki_title
        WHERE w.wiki_title IS NOT NULL AND f.wiki_title IS NULL
    """)]
    looked_up = 0
    for start in range(0, len(titles), 50):
        batch = titles[start:start + 50]
        if progress:
            progress(f"Looking up genres for films {start + 1:,}–{start + len(batch):,} of {len(titles):,}…")
        data = client.get_json(WIKIPEDIA_API, {"action": "query", "titles": "|".join(batch),
                                               "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1})
        query = data.get("query", {})
        rename = {item["from"]: item["to"] for item in query.get("normalized", [])}
        for item in query.get("redirects", []):
            rename[item["from"]] = item["to"]
        page_qids = {page.get("title"): page.get("pageprops", {}).get("wikibase_item") for page in query.get("pages", [])}

        def resolve(title: str) -> str | None:
            seen = 0
            while title in rename and seen < 5:
                title = rename[title]
                seen += 1
            return page_qids.get(title)

        qids = {title: resolve(title) for title in batch}
        wanted = sorted({qid for qid in qids.values() if qid})
        claims: dict[str, dict] = {}
        if wanted:
            entity_data = client.get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(wanted), "props": "claims"})
            for qid, entity in entity_data.get("entities", {}).items():
                claims[qid] = entity.get("claims", {})

        genre_ids: dict[str, list[str]] = {}
        release_dates: dict[str, str | None] = {}
        for qid, entity_claims in claims.items():
            ids = []
            for claim in entity_claims.get("P136", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                if isinstance(value, dict) and value.get("id"):
                    ids.append(value["id"])
            genre_ids[qid] = ids
            dates = []
            for claim in entity_claims.get("P577", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                if isinstance(value, dict) and value.get("time"):
                    match = re.match(r"[+-](\d{4})-(\d{2})-(\d{2})", value["time"])
                    if match:
                        dates.append(f"{match.group(1)}-{max(1, int(match.group(2))):02d}-{max(1, int(match.group(3))):02d}")
            release_dates[qid] = min(dates) if dates else None

        all_genre_ids = sorted({gid for ids in genre_ids.values() for gid in ids})
        labels: dict[str, str] = {}
        for label_start in range(0, len(all_genre_ids), 50):
            label_data = client.get_json(WIKIDATA_API, {"action": "wbgetentities", "props": "labels", "languages": "en",
                                                        "ids": "|".join(all_genre_ids[label_start:label_start + 50])})
            for gid, entity in label_data.get("entities", {}).items():
                label = entity.get("labels", {}).get("en", {}).get("value")
                if label:
                    labels[gid] = label

        now = datetime.now().isoformat(timespec="seconds")
        records = []
        for title in batch:
            qid = qids.get(title)
            genre_labels = [labels[gid] for gid in genre_ids.get(qid, []) if gid in labels] if qid else []
            records.append((title, qid, release_dates.get(qid) if qid else None, "|".join(genre_labels),
                            "|".join(map_genre_labels(genre_labels)), now))
        connection.executemany("INSERT OR REPLACE INTO films VALUES (?, ?, ?, ?, ?, ?)", records)
        connection.commit()
        looked_up += len(batch)
    return looked_up


def _existing_pages(client: _Client, titles: list[str]) -> dict[str, str | None]:
    """Check up to 50 titles per request; returns title -> resolved page title, or None if the page is missing."""
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


def scrape_box_office(countries: list[str], first_year: int, last_year: int,
                      refresh_existing: bool = False, progress=None, workers: int = 3) -> dict[str, int]:
    """Scrape number-one weekend tables for each country and year, then look up film genres.

    Fast path: page existence is checked 50 titles per request, then only existing pages are
    downloaded, a few at a time. Each page is saved as soon as it arrives, so stopping the run
    (the Cancel button) keeps everything downloaded so far. Years already saved are skipped
    unless ``refresh_existing`` is set; the two most recent years are always refreshed.
    """
    if not SCRAPING_AVAILABLE:
        raise RuntimeError(f"Scraping needs two extra packages. Stop the app, run `{INSTALL_HINT}` in the same terminal, then start it again")
    client = _Client(pause=0.1)
    connection = _connect()
    current_year = date_type.today().year
    summary = {"pages": 0, "missing": 0, "errors": 0, "weekends": 0, "skipped": 0, "films": 0}
    executor = None
    try:
        done = {(country, year) for country, year in connection.execute("SELECT country, year FROM scrape_log")}
        jobs = []
        for country in countries:
            for year in range(first_year, last_year + 1):
                if (country, year) in done and not refresh_existing and year < current_year - 1:
                    summary["skipped"] += 1
                else:
                    jobs.append((country, year, _page_title(country, year)))
        if progress:
            progress(f"Checking which of {len(jobs):,} pages exist…", 0.02)
        existing = _existing_pages(client, [title for _, _, title in jobs]) if jobs else {}

        scraped_at = datetime.now().isoformat(timespec="seconds")
        to_fetch = []
        for country, year, title in jobs:
            if existing.get(title) is None:
                summary["missing"] += 1
                connection.execute("INSERT OR REPLACE INTO scrape_log VALUES (?, ?, 'missing', 0, ?)", (country, year, scraped_at))
            else:
                to_fetch.append((country, year, title, existing[title]))
        connection.commit()

        local = threading.local()

        def fetch(job):
            if not hasattr(local, "client"):
                local.client = _Client(pause=0.1)
            country, year, title, resolved = job
            return job, parse_number_one_page(_fetch_page_html(local.client, resolved) or "", year)

        executor = ThreadPoolExecutor(max_workers=max(1, workers))
        futures = [executor.submit(fetch, job) for job in to_fetch]
        for finished, future in enumerate(as_completed(futures), start=1):
            try:
                (country, year, title, _), rows = future.result()
            except Exception:
                summary["errors"] += 1
                continue
            connection.executemany(
                "INSERT OR REPLACE INTO weekends VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(country, row["weekend_date"], row["film_title"], row["wiki_title"], row["gross_text"],
                  row["gross_value"], title) for row in rows])
            connection.execute("INSERT OR REPLACE INTO scrape_log VALUES (?, ?, ?, ?, ?)",
                               (country, year, "ok" if rows else "empty", len(rows), datetime.now().isoformat(timespec="seconds")))
            connection.commit()
            summary["pages"] += 1
            summary["weekends"] += len(rows)
            if progress:
                progress(f"Downloaded {finished}/{len(to_fetch)} pages · {country} {year} · {summary['weekends']:,} weekends saved",
                         0.05 + 0.85 * finished / max(len(to_fetch), 1))
        executor.shutdown(wait=True)
        executor = None
        if progress:
            progress("Looking up film genres on Wikidata…", 0.92)
        summary["films"] = _lookup_film_genres(client, connection,
                                               (lambda text: progress(text, 0.95)) if progress else None)
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


def build_movielens_index(movies: pd.DataFrame) -> dict[str, list[tuple[int | None, str]]]:
    """movies: columns title, genres ('Action|Comedy'). Returns normalized title -> [(year, genres)]."""
    index: dict[str, list[tuple[int | None, str]]] = {}
    for title, genres in zip(movies["title"], movies["genres"]):
        variants, year = movielens_title_variants(title)
        for variant in variants:
            index.setdefault(variant, []).append((year, genres))
    return index


def attach_genres(weekends: pd.DataFrame, films: pd.DataFrame,
                  movielens_index: dict[str, list[tuple[int | None, str]]]) -> pd.DataFrame:
    """Add 'genres' (pipe list) and 'genre_source' to weekend rows."""
    if weekends.empty:
        return weekends.assign(genres=pd.Series(dtype=str), genre_source=pd.Series(dtype=str))
    film_genres = dict(zip(films["wiki_title"], films["mapped_genres"])) if not films.empty else {}
    release_dates = dict(zip(films["wiki_title"], films["release_date"])) if not films.empty else {}
    first_seen = weekends.groupby(weekends["wiki_title"].fillna(weekends["film_title"]))["weekend_date"].min()
    genres_out, sources, release_out = [], [], []
    cache: dict[tuple[str, int], tuple[str, str, int | None]] = {}
    matched_years = []
    wiki_titles = [value if isinstance(value, str) and value else None for value in weekends["wiki_title"]]
    for film_title, wiki_title, weekend in zip(weekends["film_title"], wiki_titles, weekends["weekend_date"]):
        key_title = wiki_title or film_title
        year = int(first_seen.get(key_title, weekend).year)
        cache_key = (key_title, year)
        if cache_key not in cache:
            match = None
            for candidate in {normalize_title(film_title), normalize_title(wiki_title or "")} - {""}:
                options = movielens_index.get(candidate, [])
                close = [(genres, option_year) for option_year, genres in options if option_year is None or abs(option_year - year) <= 1]
                if close:
                    match = close[0]
                    break
            if match and match[0] != "(no genres listed)":
                cache[cache_key] = (match[0], "MovieLens", match[1])
            elif wiki_title and film_genres.get(wiki_title):
                cache[cache_key] = (film_genres[wiki_title], "Wikidata", None)
            else:
                cache[cache_key] = ("", "Unknown", None)
        genres, source, matched_year = cache[cache_key]
        genres_out.append(genres)
        sources.append(source)
        matched_years.append(matched_year)
        release_out.append(release_dates.get(wiki_title))
    return weekends.assign(genres=genres_out, genre_source=sources, release_date=release_out, movielens_year=matched_years)


# --------------------------------------------------------------------------- box office as a dashboard data source

def build_box_office_snapshot(movielens_db: str, countries: tuple[str, ...] = ()) -> str | None:
    """Write the scraped weekends into a database with the same tables as a MovieLens snapshot.

    Each #1 weekend in one country becomes one row of ``ratings``: user_id is the country,
    timestamp is the weekend end date and rating is empty (box office has no score). Films go into
    ``movies``/``movie_genres`` with their genres, so every dashboard query works unchanged.
    The file is rebuilt only when the scrape, the country choice or the MovieLens catalog changes.
    """
    if not SCRAPE_DB.exists():
        return None
    import hashlib
    from data_store import movie_catalog

    key = f"{movielens_db}|{','.join(sorted(countries))}|{SCRAPE_DB.stat().st_mtime}|v1"
    path = SCRAPE_DIR / f"box_office_view_{hashlib.md5(key.encode()).hexdigest()[:12]}.sqlite3"
    if path.exists():
        return str(path)

    weekends, films = load_box_office()
    if weekends.empty:
        return None
    if countries:
        weekends = weekends[weekends["country"].isin(countries)]
        if weekends.empty:
            return None
    weekends = attach_genres(weekends, films, build_movielens_index(movie_catalog(movielens_db)))
    weekends = weekends.assign(film_key=weekends["wiki_title"].where(weekends["wiki_title"].notna(), weekends["film_title"]))

    film_rows, genre_rows, film_ids = [], [], {}
    first_seen = weekends.groupby("film_key")["weekend_date"].min()
    for film_key, group in weekends.groupby("film_key", sort=False):
        first = group.iloc[0]
        release_year = None
        if isinstance(first["release_date"], str) and first["release_date"][:4].isdigit():
            release_year = int(first["release_date"][:4])
        elif pd.notna(first["movielens_year"]):
            release_year = int(first["movielens_year"])
        else:
            release_year = int(first_seen[film_key].year)
        film_id = len(film_ids) + 1
        film_ids[film_key] = film_id
        film_rows.append((film_id, f"{first['film_title']} ({release_year})", release_year))
        for genre in [genre for genre in str(first["genres"]).split("|") if genre]:
            genre_rows.append((film_id, genre, release_year))

    country_ids = {country: index for index, country in enumerate(COUNTRIES, start=1)}
    for country in weekends["country"].unique():
        country_ids.setdefault(country, len(country_ids) + 1)
    rating_rows = [(country_ids[country], film_ids[film_key], None,
                    int(pd.Timestamp(weekend).tz_localize("UTC").timestamp()) + 12 * 3600)
                   for country, film_key, weekend in zip(weekends["country"], weekends["film_key"], weekends["weekend_date"])]

    building = Path(str(path) + ".building")
    building.unlink(missing_ok=True)
    connection = sqlite3.connect(building)
    try:
        connection.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE movies (movie_id INTEGER PRIMARY KEY, title TEXT NOT NULL, release_year INTEGER);
            CREATE TABLE movie_genres (movie_id INTEGER NOT NULL, genre TEXT NOT NULL, release_year INTEGER,
                                       PRIMARY KEY (movie_id, genre));
            CREATE TABLE ratings (user_id INTEGER NOT NULL, movie_id INTEGER NOT NULL, rating REAL, timestamp INTEGER NOT NULL);
            CREATE TABLE countries (user_id INTEGER PRIMARY KEY, country TEXT NOT NULL);
        """)
        connection.executemany("INSERT INTO movies VALUES (?, ?, ?)", film_rows)
        connection.executemany("INSERT INTO movie_genres VALUES (?, ?, ?)", genre_rows)
        connection.executemany("INSERT INTO ratings VALUES (?, ?, ?, ?)", rating_rows)
        connection.executemany("INSERT INTO countries VALUES (?, ?)", [(cid, name) for name, cid in country_ids.items()])
        connection.executescript("""
            CREATE INDEX ratings_timestamp_idx ON ratings(timestamp);
            CREATE INDEX ratings_movie_idx ON ratings(movie_id);
            CREATE INDEX movie_genres_genre_idx ON movie_genres(genre);
        """)
        known = (weekends["genre_source"] != "Unknown").mean()
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", {
            "name": "Box office #1 weekends (Wikipedia)",
            "source": "Scraped from Wikipedia box office number-one lists",
            "imported": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            "ratings": str(len(rating_rows)),
            "movies": str(len(film_rows)),
            "genre_coverage": f"{known:.3f}",
        }.items())
        connection.commit()
    finally:
        connection.close()
    building.replace(path)
    old_views = sorted(SCRAPE_DIR.glob("box_office_view_*.sqlite3"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in old_views[8:]:
        old.unlink(missing_ok=True)
    return str(path)


def scraped_countries() -> list[str]:
    if not SCRAPE_DB.exists():
        return []
    with _connect() as connection:
        return [row[0] for row in connection.execute("SELECT DISTINCT country FROM weekends ORDER BY country")]


def country_breakdown(view_db: str, genre: str | None, start_date: str, end_date: str,
                      release_years: tuple[int, int] | None = None, seasons: list[str] | None = None) -> pd.DataFrame:
    """#1 weekends and distinct #1 films per country in a box office view database."""
    from data_store import _where
    where, params = _where(start_date, end_date, release_years, seasons or [], 0.0)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    if genre:
        where += " AND g.genre = ?"
        params.append(genre)
    query = f"""
        SELECT c.country, COUNT(*) AS rating_count, COUNT(DISTINCT r.movie_id) AS film_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id JOIN countries c ON c.user_id = r.user_id{genre_join}
        WHERE {where} GROUP BY c.country ORDER BY rating_count DESC
    """
    with sqlite3.connect(view_db) as connection:
        return pd.read_sql_query(query, connection, params=params)
