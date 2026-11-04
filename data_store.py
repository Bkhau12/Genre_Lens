"""SQLite snapshots and genre-level queries for the MovieLens dashboard."""

from __future__ import annotations

import csv
import calendar
import os
import random
import re
import sqlite3
import shutil
from datetime import date as date_type, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
# Snapshots live inside the project (databases/ is ignored by git). Older versions of the app saved them in
# ../02_Databases/cache; those are still listed so nothing has to be imported again.
DATABASE_DIR = ROOT / "databases" / "cache"
LEGACY_DATABASE_DIR = ROOT.parent / "02_Databases" / "cache"
EXCLUDED_DIRS = {".venv", "__pycache__", "data", ".git", "node_modules", "archive", "databases"}


def find_movie_datasets(root: Path = ROOT) -> list[dict[str, str]]:
    """Find folders containing the two required MovieLens CSV files."""
    datasets = []
    search_roots = [root, root / "data" / "datasets"]
    for search_root in search_roots:
        if not search_root.is_dir():
            continue
        for folder, directories, files in os.walk(search_root):
            directories[:] = [name for name in directories if name not in EXCLUDED_DIRS]
            if "movies.csv" not in files:
                continue
            movies_file = Path(folder) / "movies.csv"
            ratings_file = Path(folder) / "ratings.csv"
            if ratings_file.is_file():
                datasets.append({"name": movies_file.parent.name, "path": str(movies_file.parent)})
    return sorted(datasets, key=lambda item: ("small" not in item["name"].lower(), item["name"].lower(), item["path"].lower()))


def list_snapshots() -> list[dict[str, str]]:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = []
    folders = [DATABASE_DIR] + ([LEGACY_DATABASE_DIR] if LEGACY_DATABASE_DIR.is_dir() else [])
    files = [path for folder in folders for path in folder.rglob("*.sqlite3")]
    for path in sorted(files, key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            with sqlite3.connect(path) as connection:
                meta = dict(connection.execute("SELECT key, value FROM metadata"))
            snapshots.append({
                "name": meta.get("name", path.stem),
                "source": meta.get("source", "Uploaded files"),
                "imported": meta.get("imported", "Unknown"),
                "path": str(path),
            })
        except sqlite3.Error:
            continue
    return snapshots


def _release_year(title: str) -> int | None:
    match = re.search(r"\((\d{4})\)\s*$", title.strip())
    return int(match.group(1)) if match else None


def create_snapshot(
    name: str,
    source: str,
    movies_stream,
    ratings_stream,
    progress_callback=None,
) -> str:
    """Import CSV file-like objects into a new, immutable SQLite snapshot."""
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "dataset"
    db_path = DATABASE_DIR / f"{safe_name}_{now:%Y%m%d_%H%M%S_%f}.sqlite3"
    building_path = Path(str(db_path) + ".building")
    connection = sqlite3.connect(building_path)
    try:
        connection.executescript("""
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE movies (
                movie_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                release_year INTEGER
            );
            CREATE TABLE movie_genres (
                movie_id INTEGER NOT NULL,
                genre TEXT NOT NULL,
                release_year INTEGER,
                PRIMARY KEY (movie_id, genre)
            );
            CREATE TABLE ratings (
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                timestamp INTEGER NOT NULL
            );
        """)

        movies_stream.seek(0)
        movie_reader = csv.reader(line.decode("utf-8-sig") if isinstance(line, bytes) else line for line in movies_stream)
        movie_headers = next(movie_reader, [])
        required_movie_columns = {"movieId", "title", "genres"}
        if not required_movie_columns.issubset(movie_headers):
            raise ValueError("movies.csv must include movieId, title, and genres columns.")
        movie_columns = {column: movie_headers.index(column) for column in required_movie_columns}
        movie_rows, genre_rows = [], []
        for row in movie_reader:
            if len(row) <= max(movie_columns.values()):
                continue
            movie_id = int(row[movie_columns["movieId"]])
            title = row[movie_columns["title"]].strip()
            release_year = _release_year(title)
            movie_rows.append((movie_id, title, release_year))
            for genre in (row[movie_columns["genres"]] or "").split("|"):
                genre = genre.strip()
                if genre and genre.lower() != "(no genres listed)":
                    genre_rows.append((movie_id, genre, release_year))
        connection.executemany("INSERT INTO movies VALUES (?, ?, ?)", movie_rows)
        connection.executemany("INSERT INTO movie_genres VALUES (?, ?, ?)", genre_rows)
        connection.commit()

        ratings_stream.seek(0)
        rating_reader = csv.reader(line.decode("utf-8-sig") if isinstance(line, bytes) else line for line in ratings_stream)
        rating_headers = next(rating_reader, [])
        required_rating_columns = {"userId", "movieId", "rating", "timestamp"}
        if not required_rating_columns.issubset(rating_headers):
            raise ValueError("ratings.csv must include userId, movieId, rating, and timestamp columns.")
        rating_columns = {column: rating_headers.index(column) for column in required_rating_columns}

        batch, inserted = [], 0
        for row in rating_reader:
            if len(row) <= max(rating_columns.values()):
                continue
            batch.append(tuple(row[rating_columns[column]] for column in ("userId", "movieId", "rating", "timestamp")))
            if len(batch) >= 100_000:
                connection.executemany("INSERT INTO ratings VALUES (?, ?, ?, ?)", batch)
                inserted += len(batch)
                batch.clear()
                if progress_callback:
                    progress_callback(inserted)
        if batch:
            connection.executemany("INSERT INTO ratings VALUES (?, ?, ?, ?)", batch)
            inserted += len(batch)
        if progress_callback:
            progress_callback(inserted)
        connection.executescript("""
            CREATE INDEX ratings_timestamp_idx ON ratings(timestamp);
            CREATE INDEX ratings_movie_idx ON ratings(movie_id);
            CREATE INDEX ratings_user_idx ON ratings(user_id);
            CREATE INDEX movies_release_year_idx ON movies(release_year);
            CREATE INDEX movie_genres_genre_idx ON movie_genres(genre);
        """)
        metadata = {
            "name": name,
            "source": source,
            "imported": now.strftime("%Y-%m-%d %H:%M %Z"),
            "ratings": str(inserted),
            "movies": str(len(movie_rows)),
        }
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        connection.commit()
        connection.close()
        os.replace(building_path, db_path)
    except Exception:
        connection.close()
        building_path.unlink(missing_ok=True)
        raise
    return str(db_path)


def metadata(db_path: str) -> dict[str, str]:
    with sqlite3.connect(db_path) as connection:
        return dict(connection.execute("SELECT key, value FROM metadata"))


def create_release_year_partitions(db_path: str, progress_callback=None) -> list[str]:
    """Create smaller databases split by movie release year from an imported snapshot."""
    source_meta = metadata(db_path)
    source_name = source_meta.get("name", Path(db_path).stem)
    source_path = Path(db_path).resolve()
    partitions = [
        ("2000-2023", 2000, 2024),
        ("1874-1999", 1874, 2000),
    ]
    created_paths: list[Path] = []
    building_paths: list[Path] = []
    imported_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")

    try:
        for label, start_year, end_year in partitions:
            folder = DATABASE_DIR / label
            folder.mkdir(parents=True, exist_ok=True)
            database_path = folder / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', source_name).strip('_') or 'dataset'}_{stamp}.sqlite3"
            building_path = Path(str(database_path) + ".building")
            building_paths.append(building_path)
            if progress_callback:
                progress_callback(f"Preparing the {label} film database…")

            connection = sqlite3.connect(building_path)
            try:
                connection.execute("PRAGMA journal_mode = OFF")
                connection.execute("PRAGMA synchronous = OFF")
                connection.execute("PRAGMA temp_store = MEMORY")
                connection.execute("ATTACH DATABASE ? AS source", (str(source_path),))
                connection.executescript("""
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE movies (
                        movie_id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        release_year INTEGER
                    );
                    CREATE TABLE movie_genres (
                        movie_id INTEGER NOT NULL,
                        genre TEXT NOT NULL,
                        release_year INTEGER,
                        PRIMARY KEY (movie_id, genre)
                    );
                    CREATE TABLE ratings (
                        user_id INTEGER NOT NULL,
                        movie_id INTEGER NOT NULL,
                        rating REAL NOT NULL,
                        timestamp INTEGER NOT NULL
                    );
                """)
                connection.execute(
                    "INSERT INTO movies SELECT movie_id, title, release_year FROM source.movies WHERE release_year >= ? AND release_year < ?",
                    (start_year, end_year),
                )
                connection.execute(
                    "INSERT INTO movie_genres SELECT g.movie_id, g.genre, g.release_year FROM source.movie_genres g JOIN source.movies m ON m.movie_id = g.movie_id WHERE m.release_year >= ? AND m.release_year < ?",
                    (start_year, end_year),
                )
                if progress_callback:
                    progress_callback(f"Copying reviews for films released {label}…")
                connection.execute(
                    "INSERT INTO ratings SELECT r.user_id, r.movie_id, r.rating, r.timestamp FROM source.movies m JOIN source.ratings r ON r.movie_id = m.movie_id WHERE m.release_year >= ? AND m.release_year < ?",
                    (start_year, end_year),
                )
                rating_count = connection.execute("SELECT changes()").fetchone()[0]
                movie_count = connection.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
                connection.executescript("""
                    CREATE INDEX ratings_timestamp_idx ON ratings(timestamp);
                    CREATE INDEX ratings_movie_idx ON ratings(movie_id);
                    CREATE INDEX ratings_user_idx ON ratings(user_id);
                    CREATE INDEX movies_release_year_idx ON movies(release_year);
                    CREATE INDEX movie_genres_genre_idx ON movie_genres(genre);
                """)
                new_metadata = dict(source_meta)
                new_metadata.update({
                    "name": f"{source_name} · {label}",
                    "source": f"Release-year split from {source_name}",
                    "imported": imported_at,
                    "ratings": str(rating_count),
                    "movies": str(movie_count),
                    "release_years": label,
                })
                connection.executemany("INSERT INTO metadata VALUES (?, ?)", new_metadata.items())
                connection.commit()
                connection.close()
                os.replace(building_path, database_path)
                created_paths.append(database_path)
            except Exception:
                connection.close()
                raise
        return [str(path) for path in created_paths]
    except Exception:
        for path in [*building_paths, *created_paths]:
            path.unlink(missing_ok=True)
        raise


def split_dataset_folder_by_release_year(
    source_folder: str,
    progress_callback=None,
    release_year_ranges: list[tuple[str, int, int]] | None = None,
    output_prefix: str | None = None,
) -> list[dict[str, object]]:
    """Create two import-ready CSV folders, partitioning rows by movie release year."""
    source = Path(source_folder).resolve()
    movies_path = source / "movies.csv"
    ratings_path = source / "ratings.csv"
    if not movies_path.is_file() or not ratings_path.is_file():
        raise ValueError("The selected folder must contain movies.csv and ratings.csv.")

    release_year_ranges = release_year_ranges or [
        ("2000-2023", 2000, 2024),
        ("1874-1999", 1874, 2000),
    ]
    output_prefix = output_prefix or source.name
    partitions = [
        {
            "folder": source.parent / f"{output_prefix}-{label}",
            "label": label.replace("-", "–"),
            "start": start_year,
            "end": end_year,
        }
        for label, start_year, end_year in release_year_ranges
    ]
    for partition in partitions:
        if partition["folder"].exists():
            raise FileExistsError(f"Output folder already exists: {partition['folder']}")
        partition["building"] = partition["folder"].with_name(f".{partition['folder'].name}.building")
        if partition["building"].exists():
            raise FileExistsError(f"Temporary output folder already exists: {partition['building']}")
        partition["building"].mkdir()
        partition["movies"] = 0
        partition["ratings"] = 0

    movie_partition: dict[str, int] = {}
    try:
        if progress_callback:
            progress_callback("Sorting movies by release year…")
        with movies_path.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.reader(source_file)
            header = next(reader, [])
            required = {"movieId", "title", "genres"}
            if not required.issubset(header):
                raise ValueError("movies.csv must include movieId, title, and genres columns.")
            movie_id_index, title_index = header.index("movieId"), header.index("title")
            outputs = [
                (partition["building"] / "movies.csv").open("w", encoding="utf-8", newline="")
                for partition in partitions
            ]
            try:
                writers = [csv.writer(file) for file in outputs]
                for writer in writers:
                    writer.writerow(header)
                for row in reader:
                    release_year = _release_year(row[title_index]) if len(row) > title_index else None
                    if release_year is None:
                        continue
                    for index, partition in enumerate(partitions):
                        if partition["start"] <= release_year < partition["end"]:
                            movie_id = row[movie_id_index]
                            movie_partition[movie_id] = index
                            partition["movies"] += 1
                            writers[index].writerow(row)
                            break
            finally:
                for file in outputs:
                    file.close()

        if progress_callback:
            progress_callback("Splitting reviews by each film’s release year…")
        with ratings_path.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.reader(source_file)
            header = next(reader, [])
            if "movieId" not in header:
                raise ValueError("ratings.csv must include a movieId column.")
            movie_id_index = header.index("movieId")
            outputs = [
                (partition["building"] / "ratings.csv").open("w", encoding="utf-8", newline="")
                for partition in partitions
            ]
            try:
                writers = [csv.writer(file) for file in outputs]
                for writer in writers:
                    writer.writerow(header)
                for row_number, row in enumerate(reader, start=1):
                    if len(row) > movie_id_index:
                        partition_index = movie_partition.get(row[movie_id_index])
                        if partition_index is not None:
                            writers[partition_index].writerow(row)
                            partitions[partition_index]["ratings"] += 1
                    if progress_callback and row_number % 500_000 == 0:
                        progress_callback(f"Processed {row_number:,} reviews…")
            finally:
                for file in outputs:
                    file.close()

        for filename in ("links.csv", "tags.csv"):
            related_path = source / filename
            if not related_path.is_file():
                continue
            with related_path.open("r", encoding="utf-8-sig", newline="") as source_file:
                reader = csv.reader(source_file)
                header = next(reader, [])
                if "movieId" not in header:
                    continue
                movie_id_index = header.index("movieId")
                outputs = [
                    (partition["building"] / filename).open("w", encoding="utf-8", newline="")
                    for partition in partitions
                ]
                try:
                    writers = [csv.writer(file) for file in outputs]
                    for writer in writers:
                        writer.writerow(header)
                    for row in reader:
                        if len(row) > movie_id_index:
                            partition_index = movie_partition.get(row[movie_id_index])
                            if partition_index is not None:
                                writers[partition_index].writerow(row)
                finally:
                    for file in outputs:
                        file.close()

        readme = source / "README.txt"
        if readme.is_file():
            for partition in partitions:
                shutil.copy2(readme, partition["building"] / "README.txt")

        for partition in partitions:
            os.rename(partition["building"], partition["folder"])
        return [{key: partition[key] for key in ("folder", "label", "movies", "ratings")} for partition in partitions]
    except Exception:
        for partition in partitions:
            building = partition.get("building")
            if building and building.exists():
                shutil.rmtree(building)
        raise


def create_rating_sample_folders(
    source_folder: str,
    sample_sizes: tuple[int, ...] = (500_000, 1_000_000),
    seed: int = 32,
    progress_callback=None,
) -> list[dict[str, object]]:
    """Create deterministic, uniformly sampled MovieLens CSV folders."""
    source = Path(source_folder).resolve()
    movies_path = source / "movies.csv"
    ratings_path = source / "ratings.csv"
    if not movies_path.is_file() or not ratings_path.is_file():
        raise ValueError("The selected folder must contain movies.csv and ratings.csv.")
    if not sample_sizes or any(size <= 0 for size in sample_sizes):
        raise ValueError("Sample sizes must be positive.")
    if tuple(sorted(set(sample_sizes))) != tuple(sorted(sample_sizes)):
        raise ValueError("Sample sizes must be unique.")

    largest = max(sample_sizes)
    rng = random.Random(seed)
    reservoir: list[int] = []
    total_ratings = 0
    if progress_callback:
        progress_callback("Selecting a representative sample of ratings…")
    with ratings_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.reader(source_file)
        header = next(reader, [])
        required = {"userId", "movieId", "rating", "timestamp"}
        if not required.issubset(header):
            raise ValueError("ratings.csv must include userId, movieId, rating, and timestamp columns.")
        required_row_width = max(header.index(column) for column in required)
        for row in reader:
            if len(row) <= required_row_width:
                continue
            if total_ratings < largest:
                reservoir.append(total_ratings)
            else:
                replacement = rng.randrange(total_ratings + 1)
                if replacement < largest:
                    reservoir[replacement] = total_ratings
            total_ratings += 1
            if progress_callback and total_ratings % 1_000_000 == 0:
                progress_callback(f"Scanned {total_ratings:,} ratings…")
    if total_ratings < largest:
        raise ValueError(f"The source has only {total_ratings:,} ratings; {largest:,} were requested.")

    sample_rng = random.Random(seed + 1)
    sampled_indexes = {
        size: sorted(sample_rng.sample(reservoir, size))
        for size in sorted(sample_sizes)
    }
    partitions = []
    for size in sorted(sample_sizes):
        if size % 1_000_000 == 0:
            label = f"{size // 1_000_000}m"
        elif size % 1_000 == 0:
            label = f"{size // 1_000}k"
        else:
            label = str(size)
        folder = source.parent / f"{source.name}-{label}"
        building = folder.with_name(f".{folder.name}.building")
        if folder.exists():
            raise FileExistsError(f"Output folder already exists: {folder}")
        if building.exists():
            raise FileExistsError(f"Temporary output folder already exists: {building}")
        building.mkdir()
        partitions.append({
            "folder": folder,
            "building": building,
            "label": label,
            "size": size,
            "movies": 0,
            "ratings": 0,
            "indexes": sampled_indexes[size],
        })

    created_folders: list[Path] = []
    try:
        partition_movie_ids: list[set[str]] = [set() for _ in partitions]
        pointers = [0] * len(partitions)
        with ratings_path.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.reader(source_file)
            header = next(reader, [])
            movie_id_index = header.index("movieId")
            required_row_width = max(header.index(column) for column in {"userId", "movieId", "rating", "timestamp"})
            outputs = [(partition["building"] / "ratings.csv").open("w", encoding="utf-8", newline="")
                       for partition in partitions]
            try:
                writers = [csv.writer(file) for file in outputs]
                for writer in writers:
                    writer.writerow(header)
                row_index = 0
                for row in reader:
                    if len(row) <= required_row_width:
                        continue
                    for partition_index, partition in enumerate(partitions):
                        indexes = partition["indexes"]
                        if pointers[partition_index] < len(indexes) and row_index == indexes[pointers[partition_index]]:
                            writers[partition_index].writerow(row)
                            pointers[partition_index] += 1
                            partition["ratings"] += 1
                            partition_movie_ids[partition_index].add(row[movie_id_index])
                    row_index += 1
                    if progress_callback and row_index % 1_000_000 == 0:
                        progress_callback(f"Writing sample folders: scanned {row_index:,} ratings…")
            finally:
                for file in outputs:
                    file.close()

        if progress_callback:
            progress_callback("Copying the matching film catalog and related data…")
        with movies_path.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.reader(source_file)
            header = next(reader, [])
            movie_id_index = header.index("movieId")
            outputs = [(partition["building"] / "movies.csv").open("w", encoding="utf-8", newline="")
                       for partition in partitions]
            try:
                writers = [csv.writer(file) for file in outputs]
                for writer in writers:
                    writer.writerow(header)
                for row in reader:
                    if len(row) <= movie_id_index:
                        continue
                    for partition_index, writer in enumerate(writers):
                        if row[movie_id_index] in partition_movie_ids[partition_index]:
                            writer.writerow(row)
                for partition_index, partition in enumerate(partitions):
                    partition["movies"] = len(partition_movie_ids[partition_index])
            finally:
                for file in outputs:
                    file.close()

        for filename in ("links.csv", "tags.csv"):
            related_path = source / filename
            if not related_path.is_file():
                continue
            with related_path.open("r", encoding="utf-8-sig", newline="") as source_file:
                reader = csv.reader(source_file)
                header = next(reader, [])
                if "movieId" not in header:
                    continue
                movie_id_index = header.index("movieId")
                outputs = [(partition["building"] / filename).open("w", encoding="utf-8", newline="")
                           for partition in partitions]
                try:
                    writers = [csv.writer(file) for file in outputs]
                    for writer in writers:
                        writer.writerow(header)
                    for row in reader:
                        if len(row) > movie_id_index:
                            for partition_index, writer in enumerate(writers):
                                if row[movie_id_index] in partition_movie_ids[partition_index]:
                                    writer.writerow(row)
                finally:
                    for file in outputs:
                        file.close()

        readme = source / "README.txt"
        if readme.is_file():
            for partition in partitions:
                shutil.copy2(readme, partition["building"] / "README.txt")

        for partition in partitions:
            partition["folder"].mkdir()
            created_folders.append(partition["folder"])
            for staged_file in partition["building"].iterdir():
                os.replace(staged_file, partition["folder"] / staged_file.name)
            partition["building"].rmdir()
        return [{key: partition[key] for key in ("folder", "label", "movies", "ratings")} for partition in partitions]
    except Exception:
        for partition in partitions:
            building = partition["building"]
            if building.exists():
                shutil.rmtree(building)
        for folder in created_folders:
            if folder.exists():
                shutil.rmtree(folder)
        raise


def dataset_profile(db_path: str) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        result = connection.execute("""
            SELECT COUNT(*), COUNT(DISTINCT user_id), MIN(date(timestamp, 'unixepoch')),
                   MAX(date(timestamp, 'unixepoch'))
            FROM ratings
        """).fetchone()
        release = connection.execute("SELECT MIN(release_year), MAX(release_year), COUNT(DISTINCT release_year) FROM movies").fetchone()
        genres = [row[0] for row in connection.execute("SELECT DISTINCT genre FROM movie_genres ORDER BY genre")]
    return {"ratings": result[0], "reviewers": result[1], "first_date": result[2], "last_date": result[3],
            "release_min": release[0], "release_max": release[1], "release_year_count": release[2], "genres": genres}


def _where(start_date: str, end_date: str, release_years: tuple[int, int] | None, seasons: list[str],
           minimum_score: float = 0.0):
    start_epoch = int(datetime.combine(date_type.fromisoformat(start_date), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_epoch = int(datetime.combine(date_type.fromisoformat(end_date), datetime.min.time(), tzinfo=timezone.utc).timestamp()) + 86_400
    clauses = ["r.timestamp >= ? AND r.timestamp < ?"]
    params: list[object] = [start_epoch, end_epoch]
    if minimum_score > 0:
        clauses.append("r.rating >= ?")
        params.append(minimum_score)
    if release_years:
        clauses.append("m.release_year BETWEEN ? AND ?")
        params.extend(release_years)
    if seasons:
        season_months = {"Spring": (3, 4, 5), "Summer": (6, 7, 8), "Autumn": (9, 10, 11), "Winter": (12, 1, 2)}
        months = sorted({month for season in seasons for month in season_months[season]})
        clauses.append("CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")")
        params.extend(months)
    return " AND ".join(clauses), params


def genre_summary(db_path: str, start_date: str, end_date: str,
                  release_years: tuple[int, int] | None = None, seasons: list[str] | None = None,
                  minimum_score: float = 0.0) -> pd.DataFrame:
    where, params = _where(start_date, end_date, release_years, seasons or [], minimum_score)
    query = f"""
        SELECT g.genre, AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count,
               COUNT(DISTINCT m.release_year) AS release_year_count,
               COUNT(DISTINCT r.movie_id) AS film_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id
        JOIN movie_genres g ON g.movie_id = r.movie_id
        WHERE {where}
        GROUP BY g.genre ORDER BY average_rating DESC
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def filtered_totals(db_path: str, start_date: str, end_date: str,
                    release_years: tuple[int, int] | None = None, seasons: list[str] | None = None,
                    minimum_score: float = 0.0) -> dict[str, object]:
    where, params = _where(start_date, end_date, release_years, seasons or [], minimum_score)
    query = f"""
        SELECT COUNT(*), AVG(r.rating), COUNT(DISTINCT r.user_id),
               COUNT(DISTINCT r.movie_id), COUNT(DISTINCT m.release_year)
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id WHERE {where}
    """
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(query, params).fetchone()
    return {"rating_count": row[0], "average_rating": row[1], "reviewer_count": row[2],
            "film_count": row[3], "release_year_count": row[4]}


def time_comparison(db_path: str, dimension: str, genre: str | None, start_date: str, end_date: str,
                    release_years: tuple[int, int] | None = None,
                    seasons: list[str] | None = None, minimum_score: float = 0.0) -> pd.DataFrame:
    """Compare ratings grouped by a selected time dimension."""
    expressions = {
        "Rating year": "CAST(strftime('%Y', r.timestamp, 'unixepoch') AS INTEGER)",
        "Release year": "m.release_year",
        "Month": "CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER)",
        "Season": "CASE CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) WHEN 12 THEN 'Winter' WHEN 1 THEN 'Winter' WHEN 2 THEN 'Winter' WHEN 3 THEN 'Spring' WHEN 4 THEN 'Spring' WHEN 5 THEN 'Spring' WHEN 6 THEN 'Summer' WHEN 7 THEN 'Summer' WHEN 8 THEN 'Summer' ELSE 'Autumn' END",
        "Weekday": "CAST(strftime('%w', r.timestamp, 'unixepoch') AS INTEGER)",
        "Day of month": "CAST(strftime('%d', r.timestamp, 'unixepoch') AS INTEGER)",
    }
    if dimension not in expressions:
        raise ValueError("Unsupported comparison dimension.")
    where, params = _where(start_date, end_date, release_years, seasons or [], minimum_score)
    if dimension == "Release year":
        where += " AND m.release_year IS NOT NULL"
    if genre:
        where += " AND g.genre = ?"
        params.append(genre)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    group_expression = expressions[dimension]
    query = f"""
        SELECT {group_expression} AS time_group, AVG(r.rating) AS average_rating,
               COUNT(*) AS rating_count, COUNT(DISTINCT r.user_id) AS reviewer_count,
               COUNT(DISTINCT m.release_year) AS release_year_count,
               COUNT(DISTINCT strftime('%Y-%m', r.timestamp, 'unixepoch')) AS date_occurrences,
               COUNT(*) * 1.0 / COUNT(DISTINCT strftime('%Y-%m', r.timestamp, 'unixepoch')) AS average_per_occurrence
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id{genre_join}
        WHERE {where}
        GROUP BY time_group ORDER BY time_group
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def genre_trend(db_path: str, genre: str, start_date: str, end_date: str,
                release_years: tuple[int, int] | None = None, seasons: list[str] | None = None,
                minimum_score: float = 0.0) -> pd.DataFrame:
    where, params = _where(start_date, end_date, release_years, seasons or [], minimum_score)
    query = f"""
        SELECT strftime('%Y', r.timestamp, 'unixepoch') AS year,
               AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id
        JOIN movie_genres g ON g.movie_id = r.movie_id
        WHERE {where} AND g.genre = ?
        GROUP BY year ORDER BY year
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=[*params, genre])


def rating_distribution(db_path: str, genre: str, start_date: str, end_date: str,
                        release_years: tuple[int, int] | None = None, seasons: list[str] | None = None,
                        minimum_score: float = 0.0) -> pd.DataFrame:
    where, params = _where(start_date, end_date, release_years, seasons or [], minimum_score)
    query = f"""
        SELECT r.rating, COUNT(*) AS rating_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id
        JOIN movie_genres g ON g.movie_id = r.movie_id
        WHERE {where} AND g.genre = ?
        GROUP BY r.rating ORDER BY r.rating
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=[*params, genre])


def monthly_activity(db_path: str, genre: str | None, start_date: str, end_date: str,
                    release_years: tuple[int, int] | None = None,
                    months: list[int] | None = None, minimum_score: float = 0.0) -> pd.DataFrame:
    """Summarize rating activity by month of year for selected filters."""
    where, params = _where(start_date, end_date, release_years, [], minimum_score)
    if months:
        where += " AND CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")"
        params.extend(months)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    genre_filter = " AND g.genre = ?" if genre else ""
    if genre:
        params.append(genre)
    query = f"""
        SELECT CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) AS month,
               AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id {genre_join}
        WHERE {where}{genre_filter}
        GROUP BY month ORDER BY month
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def monthly_history(db_path: str, genre: str | None, start_date: str, end_date: str,
                    release_years: tuple[int, int] | None = None,
                    months: list[int] | None = None, minimum_score: float = 0.0) -> pd.DataFrame:
    """Summarize rating activity for each month-year pair."""
    where, params = _where(start_date, end_date, release_years, [], minimum_score)
    if months:
        where += " AND CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")"
        params.extend(months)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    genre_filter = " AND g.genre = ?" if genre else ""
    if genre:
        params.append(genre)
    query = f"""
        SELECT strftime('%Y-%m', r.timestamp, 'unixepoch') AS year_month,
               AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id {genre_join}
        WHERE {where}{genre_filter}
        GROUP BY year_month ORDER BY year_month
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def weekday_activity(db_path: str, genre: str | None, start_date: str, end_date: str,
                     release_years: tuple[int, int] | None = None,
                     months: list[int] | None = None, minimum_score: float = 0.0) -> pd.DataFrame:
    """Summarize rating activity by weekday (UTC timestamp)."""
    where, params = _where(start_date, end_date, release_years, [], minimum_score)
    if months:
        where += " AND CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")"
        params.extend(months)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    genre_filter = " AND g.genre = ?" if genre else ""
    if genre:
        params.append(genre)
    query = f"""
        SELECT CAST(strftime('%w', r.timestamp, 'unixepoch') AS INTEGER) AS weekday,
               AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id {genre_join}
        WHERE {where}{genre_filter}
        GROUP BY weekday ORDER BY weekday
    """
    with sqlite3.connect(db_path) as connection:
        result = pd.read_sql_query(query, connection, params=params)
    weekday_names = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
                     4: "Thursday", 5: "Friday", 6: "Saturday"}
    if not result.empty:
        result["weekday_name"] = result["weekday"].map(weekday_names)
    return result


def day_of_month_activity(db_path: str, genre: str | None, start_date: str, end_date: str,
                          release_years: tuple[int, int] | None = None,
                          months: list[int] | None = None, minimum_score: float = 0.0) -> pd.DataFrame:
    """Summarize rating activity by calendar day number."""
    where, params = _where(start_date, end_date, release_years, [], minimum_score)
    if months:
        where += " AND CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")"
        params.extend(months)
    genre_join = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    genre_filter = " AND g.genre = ?" if genre else ""
    if genre:
        params.append(genre)
    query = f"""
        SELECT CAST(strftime('%d', r.timestamp, 'unixepoch') AS INTEGER) AS day_of_month,
               AVG(r.rating) AS average_rating, COUNT(*) AS rating_count,
               COUNT(DISTINCT r.user_id) AS reviewer_count,
               COUNT(DISTINCT strftime('%Y-%m', r.timestamp, 'unixepoch')) AS occurrence_count,
               COUNT(*) * 1.0 / COUNT(DISTINCT strftime('%Y-%m', r.timestamp, 'unixepoch')) AS average_per_occurrence
        FROM ratings r JOIN movies m ON m.movie_id = r.movie_id {genre_join}
        WHERE {where}{genre_filter}
        GROUP BY day_of_month ORDER BY day_of_month
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date_type:
    first_weekday, _ = calendar.monthrange(year, month)
    first_occurrence = 1 + (weekday - first_weekday) % 7
    return date_type(year, month, first_occurrence + 7 * (occurrence - 1))


def _easter_sunday(year: int) -> date_type:
    """Gregorian Easter date, using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date_type(year, month, day)


# Lunar New Year (Tết / Chinese New Year) does not follow a simple rule, so dates are listed.
LUNAR_NEW_YEAR = {
    1995: (1, 31), 1996: (2, 19), 1997: (2, 7), 1998: (1, 28), 1999: (2, 16), 2000: (2, 5),
    2001: (1, 24), 2002: (2, 12), 2003: (2, 1), 2004: (1, 22), 2005: (2, 9), 2006: (1, 29),
    2007: (2, 18), 2008: (2, 7), 2009: (1, 26), 2010: (2, 14), 2011: (2, 3), 2012: (1, 23),
    2013: (2, 10), 2014: (1, 31), 2015: (2, 19), 2016: (2, 8), 2017: (1, 28), 2018: (2, 16),
    2019: (2, 5), 2020: (1, 25), 2021: (2, 12), 2022: (2, 1), 2023: (1, 22), 2024: (2, 10),
    2025: (1, 29), 2026: (2, 17), 2027: (2, 6), 2028: (1, 26), 2029: (2, 13), 2030: (2, 3),
}

SPECIAL_DATE_ORDER = ["New Year's Day", "Lunar New Year (Tết)", "Valentine's Day", "International Women's Day",
                      "St. Patrick's Day", "Easter Sunday", "Earth Day", "Mother's Day (US)", "Summer Solstice",
                      "Father's Day (US)", "Independence Day (US)", "Halloween", "Thanksgiving (US)", "Christmas Day"]


def special_dates(year: int) -> list[tuple[str, date_type]]:
    """Public list of (event name, date) for a year, used by the release planner too."""
    return _special_dates(year)


def _special_dates(year: int) -> list[tuple[str, date_type]]:
    lunar = [("Lunar New Year (Tết)", date_type(year, *LUNAR_NEW_YEAR[year]))] if year in LUNAR_NEW_YEAR else []
    return [
        ("New Year's Day", date_type(year, 1, 1)),
        *lunar,
        ("Valentine's Day", date_type(year, 2, 14)),
        ("International Women's Day", date_type(year, 3, 8)),
        ("St. Patrick's Day", date_type(year, 3, 17)),
        ("Easter Sunday", _easter_sunday(year)),
        ("Earth Day", date_type(year, 4, 22)),
        ("Mother's Day (US)", _nth_weekday(year, 5, 6, 2)),
        ("Summer Solstice", date_type(year, 6, 21)),
        ("Father's Day (US)", _nth_weekday(year, 6, 6, 3)),
        ("Independence Day (US)", date_type(year, 7, 4)),
        ("Halloween", date_type(year, 10, 31)),
        ("Thanksgiving (US)", _nth_weekday(year, 11, 3, 4)),
        ("Christmas Day", date_type(year, 12, 25)),
    ]


def special_day_activity(db_path: str, genre: str | None, start_date: str, end_date: str,
                         release_years: tuple[int, int] | None = None,
                         window_days: int = 3,
                         seasons: list[str] | None = None,
                         minimum_score: float = 0.0) -> pd.DataFrame:
    """Aggregate rating activity in annual windows around selected calendar events."""
    start = date_type.fromisoformat(start_date)
    end = date_type.fromisoformat(end_date)
    start_epoch = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_epoch = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    event_values, values_params = [], []
    for year in range(start.year, end.year + 1):
        for event_name, event_date in _special_dates(year):
            window_start = event_date - timedelta(days=window_days)
            window_end = event_date + timedelta(days=window_days + 1)
            lower = max(start_epoch, int(datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc).timestamp()))
            upper = min(end_epoch, int(datetime.combine(window_end, datetime.min.time(), tzinfo=timezone.utc).timestamp()))
            if lower < upper:
                safe_event_name = event_name.replace("'", "''")
                event_values.append(f"('{safe_event_name}', {year}, ?, ?)")
                values_params.extend((lower, upper))
    if not event_values:
        return pd.DataFrame(columns=["event", "event_year", "average_rating", "rating_count", "reviewer_count"])

    clauses, params = [], []
    if minimum_score > 0:
        clauses.append("r.rating >= ?")
        params.append(minimum_score)
    if release_years:
        clauses.append("m.release_year BETWEEN ? AND ?")
        params.extend(release_years)
    if genre:
        clauses.append("g.genre = ?")
        params.append(genre)
    if seasons:
        season_months = {"Spring": (3, 4, 5), "Summer": (6, 7, 8), "Autumn": (9, 10, 11), "Winter": (12, 1, 2)}
        months = sorted({month for season in seasons for month in season_months[season]})
        clauses.append("CAST(strftime('%m', r.timestamp, 'unixepoch') AS INTEGER) IN (" + ",".join("?" for _ in months) + ")")
        params.extend(months)
    extra_joins = " JOIN movie_genres g ON g.movie_id = r.movie_id" if genre else ""
    extra_where = " AND " + " AND ".join(clauses) if clauses else ""
    query = f"""
        WITH event_windows(event, event_year, start_ts, end_ts) AS (VALUES {','.join(event_values)})
        SELECT e.event, e.event_year, AVG(r.rating) AS average_rating,
               COUNT(*) AS rating_count, COUNT(DISTINCT r.user_id) AS reviewer_count
        FROM event_windows e
        JOIN ratings r ON r.timestamp >= e.start_ts AND r.timestamp < e.end_ts
        JOIN movies m ON m.movie_id = r.movie_id{extra_joins}
        WHERE 1 = 1{extra_where}
        GROUP BY e.event, e.event_year ORDER BY e.event, e.event_year
    """
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=[*values_params, *params])



def movie_catalog(db_path: str) -> pd.DataFrame:
    """Titles with pipe-joined genres, used to match scraped box office films to this dataset."""
    with sqlite3.connect(db_path) as connection:
        return pd.read_sql_query("""
            SELECT m.movie_id, m.title, COALESCE(GROUP_CONCAT(g.genre, '|'), '(no genres listed)') AS genres
            FROM movies m LEFT JOIN movie_genres g ON g.movie_id = m.movie_id
            GROUP BY m.movie_id
        """, connection)
