"""Fast in-memory versions of the dashboard queries.

The SQLite snapshot stays the source of truth, but each filter change used to rescan the whole
ratings table several times (about 1–3 s per query on 500k ratings). Here the ratings of a snapshot
are read into memory once, with the date parts (year, month, weekday, ...) worked out a single time,
and every query becomes a quick NumPy/pandas filter + group-by.

Every function has the same name, parameters and output columns as its twin in ``data_store.py``,
so ``main.py`` can use either one.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import date as date_type, datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
import pandas as pd

from data_store import _special_dates

SEASON_MONTHS = {"Spring": (3, 4, 5), "Summer": (6, 7, 8), "Autumn": (9, 10, 11), "Winter": (12, 1, 2)}
SEASON_OF_MONTH = {month: season for season, months in SEASON_MONTHS.items() for month in months}
WEEKDAY_NAMES = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}


class _Ratings:
    """All ratings of one snapshot as NumPy columns, sorted by timestamp."""

    def __init__(self, db_path: str):
        with sqlite3.connect(db_path) as connection:
            frame = pd.read_sql_query("""
                SELECT r.user_id, r.movie_id, r.rating, r.timestamp, m.release_year
                FROM ratings r JOIN movies m ON m.movie_id = r.movie_id
            """, connection)
            genres = pd.read_sql_query("SELECT movie_id, genre FROM movie_genres", connection)
        frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
        self.size = len(frame)
        self.user = frame["user_id"].to_numpy(np.int64)
        self.movie = frame["movie_id"].to_numpy(np.int64)
        self.rating = pd.to_numeric(frame["rating"], errors="coerce").to_numpy(np.float64)   # NaN for box office rows
        self.timestamp = frame["timestamp"].to_numpy(np.int64)
        self.release = pd.to_numeric(frame["release_year"], errors="coerce").to_numpy(np.float64)
        moments = pd.to_datetime(self.timestamp, unit="s", utc=True)
        self.year = moments.year.to_numpy(np.int64)
        self.month = moments.month.to_numpy(np.int64)
        self.day = moments.day.to_numpy(np.int64)
        self.weekday = ((moments.dayofweek.to_numpy(np.int64) + 1) % 7)   # SQLite %w: Sunday = 0
        self.year_month = self.year * 100 + self.month
        self._genres = genres
        self._genre_masks: dict[str, np.ndarray] = {}
        self._exploded = None
        self._lock = threading.Lock()

    def genre_mask(self, genre: str) -> np.ndarray:
        mask = self._genre_masks.get(genre)
        if mask is None:
            movies = self._genres.loc[self._genres["genre"] == genre, "movie_id"].to_numpy(np.int64)
            mask = np.isin(self.movie, movies)
            with self._lock:
                self._genre_masks[genre] = mask
        return mask

    def exploded(self) -> tuple[np.ndarray, np.ndarray]:
        """(rating row, genre) pairs: one entry per rating per genre of its film."""
        if self._exploded is None:
            pairs = pd.DataFrame({"movie_id": self.movie, "row": np.arange(self.size)}).merge(self._genres, on="movie_id")
            pairs = pairs.sort_values("row", kind="stable")
            with self._lock:
                self._exploded = (pairs["row"].to_numpy(np.int64), pairs["genre"].to_numpy(object))
        return self._exploded


@lru_cache(maxsize=4)
def _load(db_path: str, modified: float) -> _Ratings:
    return _Ratings(db_path)


def _data(db_path: str) -> _Ratings:
    return _load(db_path, os.path.getmtime(db_path))


def preload(db_path: str) -> None:
    """Read a snapshot into memory ahead of the first query."""
    _data(db_path)


def _epoch(day: date_type) -> int:
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def _base_mask(data: _Ratings, release_years, minimum_score: float, months=None, genre: str | None = None) -> np.ndarray:
    """Every filter except the date range (same rules as data_store._where)."""
    mask = np.ones(data.size, dtype=bool)
    if minimum_score > 0:
        mask &= data.rating >= minimum_score          # NaN compares False, like SQL NULL
    if release_years:
        mask &= (data.release >= release_years[0]) & (data.release <= release_years[1])
    if months:
        mask &= np.isin(data.month, list(months))
    if genre:
        mask &= data.genre_mask(genre)
    return mask


def _mask(data: _Ratings, start_date: str, end_date: str, release_years, minimum_score: float,
          months=None, genre: str | None = None) -> np.ndarray:
    start = _epoch(date_type.fromisoformat(start_date))
    end = _epoch(date_type.fromisoformat(end_date)) + 86_400
    mask = _base_mask(data, release_years, minimum_score, months, genre)
    lo, hi = np.searchsorted(data.timestamp, [start, end], side="left")
    mask[:lo] = False
    mask[hi:] = False
    return mask


def _season_months(seasons) -> list[int] | None:
    if not seasons:
        return None
    return sorted({month for season in seasons for month in SEASON_MONTHS[season]})


def _group(data: _Ratings, mask: np.ndarray, key: np.ndarray, key_name: str, extra: dict | None = None) -> pd.DataFrame:
    frame = pd.DataFrame({key_name: key[mask], "rating": data.rating[mask], "user": data.user[mask]})
    for name, values in (extra or {}).items():
        frame[name] = values[mask]
    aggregations = {"average_rating": ("rating", "mean"), "rating_count": ("rating", "size"), "reviewer_count": ("user", "nunique")}
    if extra and "release" in extra:
        aggregations["release_year_count"] = ("release", "nunique")
    if extra and "year_month" in extra:
        aggregations["date_occurrences"] = ("year_month", "nunique")
    result = frame.groupby(key_name, sort=True).agg(**aggregations).reset_index()
    result["rating_count"] = result["rating_count"].astype(np.int64)
    return result


# ------------------------------------------------------------------ public queries (same as data_store)

def genre_summary(db_path: str, start_date: str, end_date: str, release_years=None, seasons=None,
                  minimum_score: float = 0.0) -> pd.DataFrame:
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, _season_months(seasons))
    rows, genres = data.exploded()
    keep = mask[rows]
    rows, genres = rows[keep], genres[keep]
    frame = pd.DataFrame({"genre": genres, "rating": data.rating[rows], "user": data.user[rows],
                          "release": data.release[rows], "movie": data.movie[rows]})
    columns = ["genre", "average_rating", "rating_count", "reviewer_count", "release_year_count", "film_count"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    result = frame.groupby("genre").agg(average_rating=("rating", "mean"), rating_count=("rating", "size"),
                                        reviewer_count=("user", "nunique"), release_year_count=("release", "nunique"),
                                        film_count=("movie", "nunique")).reset_index()
    return result.sort_values("average_rating", ascending=False, kind="stable").reset_index(drop=True)[columns]


def filtered_totals(db_path: str, start_date: str, end_date: str, release_years=None, seasons=None,
                    minimum_score: float = 0.0) -> dict[str, object]:
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, _season_months(seasons))
    count = int(mask.sum())
    ratings = data.rating[mask]
    average = float(np.nanmean(ratings)) if count and not np.isnan(ratings).all() else None
    return {"rating_count": count, "average_rating": average,
            "reviewer_count": int(pd.unique(data.user[mask]).size),
            "film_count": int(pd.unique(data.movie[mask]).size),
            "release_year_count": int(pd.Series(data.release[mask]).nunique())}


def time_comparison(db_path: str, dimension: str, genre: str | None, start_date: str, end_date: str,
                    release_years=None, seasons=None, minimum_score: float = 0.0) -> pd.DataFrame:
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, _season_months(seasons), genre)
    if dimension == "Rating year":
        key = data.year
    elif dimension == "Release year":
        mask &= ~np.isnan(data.release)
        key = data.release
    elif dimension == "Month":
        key = data.month
    elif dimension == "Season":
        key = np.array([SEASON_OF_MONTH[month] for month in range(13) if month] , dtype=object)[data.month - 1]
    elif dimension == "Weekday":
        key = data.weekday
    elif dimension == "Day of month":
        key = data.day
    else:
        raise ValueError("Unsupported comparison dimension.")
    columns = ["time_group", "average_rating", "rating_count", "reviewer_count", "release_year_count",
               "date_occurrences", "average_per_occurrence"]
    if not mask.any():
        return pd.DataFrame(columns=columns)
    result = _group(data, mask, key, "time_group", {"release": data.release, "year_month": data.year_month})
    if dimension == "Release year":
        result["time_group"] = result["time_group"].astype(np.int64)
    result["average_per_occurrence"] = result["rating_count"] / result["date_occurrences"]
    return result[columns]


def genre_trend(db_path: str, genre: str, start_date: str, end_date: str, release_years=None, seasons=None,
                minimum_score: float = 0.0) -> pd.DataFrame:
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, _season_months(seasons), genre)
    if not mask.any():
        return pd.DataFrame(columns=["year", "average_rating", "rating_count", "reviewer_count"])
    result = _group(data, mask, data.year, "year")
    result["year"] = result["year"].astype(str)
    return result


def rating_distribution(db_path: str, genre: str, start_date: str, end_date: str, release_years=None, seasons=None,
                        minimum_score: float = 0.0) -> pd.DataFrame:
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, _season_months(seasons), genre)
    counts = pd.Series(data.rating[mask]).value_counts().sort_index()
    return pd.DataFrame({"rating": counts.index.to_numpy(np.float64), "rating_count": counts.to_numpy(np.int64)})


def _activity(db_path, genre, start_date, end_date, release_years, months, minimum_score, key_name, key_of, extra=None):
    data = _data(db_path)
    mask = _mask(data, start_date, end_date, release_years, minimum_score, list(months) if months else None, genre)
    if not mask.any():
        return pd.DataFrame(columns=[key_name, "average_rating", "rating_count", "reviewer_count"]), data
    return _group(data, mask, key_of(data), key_name, extra(data) if extra else None), data


def monthly_activity(db_path: str, genre: str | None, start_date: str, end_date: str, release_years=None,
                     months=None, minimum_score: float = 0.0) -> pd.DataFrame:
    result, _ = _activity(db_path, genre, start_date, end_date, release_years, months, minimum_score, "month", lambda d: d.month)
    return result


def monthly_history(db_path: str, genre: str | None, start_date: str, end_date: str, release_years=None,
                    months=None, minimum_score: float = 0.0) -> pd.DataFrame:
    result, _ = _activity(db_path, genre, start_date, end_date, release_years, months, minimum_score,
                          "year_month", lambda d: d.year_month)
    if not result.empty:
        result["year_month"] = result["year_month"].map(lambda value: f"{value // 100:04d}-{value % 100:02d}")
    return result


def weekday_activity(db_path: str, genre: str | None, start_date: str, end_date: str, release_years=None,
                     months=None, minimum_score: float = 0.0) -> pd.DataFrame:
    result, _ = _activity(db_path, genre, start_date, end_date, release_years, months, minimum_score, "weekday", lambda d: d.weekday)
    if not result.empty:
        result["weekday_name"] = result["weekday"].map(WEEKDAY_NAMES)
    return result


def day_of_month_activity(db_path: str, genre: str | None, start_date: str, end_date: str, release_years=None,
                          months=None, minimum_score: float = 0.0) -> pd.DataFrame:
    result, _ = _activity(db_path, genre, start_date, end_date, release_years, months, minimum_score, "day_of_month",
                          lambda d: d.day, lambda d: {"year_month": d.year_month})
    if result.empty:
        return pd.DataFrame(columns=["day_of_month", "average_rating", "rating_count", "reviewer_count",
                                     "occurrence_count", "average_per_occurrence"])
    result = result.rename(columns={"date_occurrences": "occurrence_count"})
    result["average_per_occurrence"] = result["rating_count"] / result["occurrence_count"]
    return result


def special_day_activity(db_path: str, genre: str | None, start_date: str, end_date: str, release_years=None,
                         window_days: int = 3, seasons=None, minimum_score: float = 0.0) -> pd.DataFrame:
    data = _data(db_path)
    start = date_type.fromisoformat(start_date)
    end = date_type.fromisoformat(end_date)
    start_epoch, end_epoch = _epoch(start), _epoch(end + timedelta(days=1))
    base = _base_mask(data, release_years, minimum_score, _season_months(seasons), genre)
    rows = []
    for year in range(start.year, end.year + 1):
        for event_name, event_date in _special_dates(year):
            lower = max(start_epoch, _epoch(event_date - timedelta(days=window_days)))
            upper = min(end_epoch, _epoch(event_date + timedelta(days=window_days + 1)))
            if lower >= upper:
                continue
            lo, hi = np.searchsorted(data.timestamp, [lower, upper], side="left")
            picked = base[lo:hi]
            count = int(picked.sum())
            if not count:
                continue
            ratings = data.rating[lo:hi][picked]
            rows.append((event_name, year, float(np.nanmean(ratings)) if not np.isnan(ratings).all() else np.nan,
                         count, int(pd.unique(data.user[lo:hi][picked]).size)))
    result = pd.DataFrame(rows, columns=["event", "event_year", "average_rating", "rating_count", "reviewer_count"])
    return result.sort_values(["event", "event_year"], kind="stable").reset_index(drop=True)
