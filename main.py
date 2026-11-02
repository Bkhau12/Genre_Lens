from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from data_store import (
    create_snapshot,
    dataset_profile,
    find_movie_datasets,
    list_snapshots,
    metadata,
    SPECIAL_DATE_ORDER,
)
# Same queries as data_store, but run in memory: the snapshot is read once, then each filter is milliseconds.
from fast_queries import (
    day_of_month_activity,
    filtered_totals,
    genre_summary,
    genre_trend,
    monthly_activity,
    monthly_history,
    preload,
    rating_distribution,
    special_day_activity,
    time_comparison,
    weekday_activity,
)
from web_scraper import (
    COUNTRIES,
    INSTALL_HINT,
    SCRAPE_DB,
    SCRAPING_AVAILABLE,
    build_box_office_snapshot,
    country_breakdown,
    coverage_table,
    scraped_countries,
    scrape_box_office,
    scrape_status,
)


st.set_page_config(page_title="Genre Lens", page_icon="🎞️", layout="wide")
px.defaults.template = "plotly_white"
PRIMARY_BLUE = "#278BCB"
GENRE_COLORS = {
    "Action": "#E76F51",
    "Adventure": "#F28E2B",
    "Animation": "#E3B341",
    "Children": "#35A78A",
    "Comedy": "#4F9ED9",
    "Crime": "#566579",
    "Documentary": "#218C83",
    "Drama": "#B83B4B",
    "Fantasy": "#8064A2",
    "Film-Noir": "#253746",
    "Horror": "#7B2D43",
    "IMAX": "#2878B5",
    "Musical": "#C94F91",
    "Mystery": "#6851A4",
    "Romance": "#D95D8C",
    "Sci-Fi": "#218C9B",
    "Thriller": "#D97732",
    "War": "#89772D",
    "Western": "#A66A3F",
}
GENRE_PALETTE = list(GENRE_COLORS.values())
px.defaults.color_discrete_sequence = GENRE_PALETTE

st.markdown("""
<style>
[data-testid="stAppViewContainer"], .stApp { background: #F1F7FC; color: #1D2939; }
[data-testid="stMainBlockContainer"] { max-width: 1480px; padding-top: 1.2rem; padding-bottom: 3.2rem; }
[data-testid="stHeader"] { background: #EAF4FB; }
[data-testid="stStatusWidget"], [data-testid="stAppDeployButton"], #MainMenu { display: none !important; visibility: hidden !important; }
[data-testid="stToolbar"] { background: transparent !important; }
[data-testid="stExpandSidebarButton"] { visibility: visible !important; opacity: 1 !important; }
[data-testid="stExpandSidebarButton"] svg { color: #1676B8 !important; fill: #1676B8 !important; }
[data-testid="stSidebar"] { background: #EAF4FB; border-right: 1px solid #C8DDEA; }
h1, h2, h3 { color: #17243A !important; letter-spacing: -.025em; }
h1 { font-size: 2.25rem !important; margin-bottom: .15rem !important; }
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"], label, p { color: #253447; }
[data-testid="stCaptionContainer"] { color: #536476; }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #C7DCEB; padding: 1rem 1.05rem; border-radius: 15px; box-shadow: 0 5px 18px rgba(39,94,132,.08); min-height: 104px; }
[data-testid="stMetricLabel"] { color: #526477; }
[data-testid="stMetricValue"] { color: #17243A; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color: #C9DDEA !important; border-radius: 16px !important; background: #FFFFFF !important; }
.stTabs [data-baseweb="tab-list"] { gap: .45rem; border-bottom: 1px solid #C7DCEB; }
.stTabs [data-baseweb="tab"] { height: 46px; color: #46596D; border-radius: 10px 10px 0 0; }
.stTabs [aria-selected="true"] { color: #075F93 !important; background: #D6ECFA !important; font-weight: 700; }
[data-testid="stDataFrame"] { border: 1px solid #C7DCEB; border-radius: 12px; overflow: hidden; }
.stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 600; border-color: #AFCBDD; color: #1E3448; }
.stButton > button[kind="primary"] { background: #278BCB; color: #FFFFFF; border-color: #278BCB; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: #176FA6; color: #075F93; }
[data-baseweb="select"] > div, [data-baseweb="input"] > div, textarea { background: #FFFFFF; border-color: #AFCBDD !important; color: #17243A !important; }
[data-testid="stSlider"] [role="slider"] { background: #278BCB !important; border-color: #278BCB !important; }
[data-testid="stPlotlyChart"] { border-radius: 10px; }
[data-testid="stAlert"] { border-radius: 10px; }
div[data-testid="stVerticalBlock"] > div:has(> [data-testid="stPlotlyChart"]) { background: #FFFFFF; border: 1px solid #C7DCEB; border-radius: 14px; padding: .35rem; box-shadow: 0 4px 16px rgba(39,94,132,.06); }
.hero-card { padding: 1.55rem 1.8rem; margin: .1rem 0 1rem; border: 1px solid #A8D3EC; border-radius: 20px; background: linear-gradient(115deg, #D9EFFC 0%, #EAF6FD 56%, #FFFFFF 100%); box-shadow: 0 8px 26px rgba(45,112,157,.10); }
.hero-eyebrow { color: #12699C; font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
.hero-title { color: #17324A; font-size: 2.15rem; font-weight: 750; letter-spacing: -.04em; line-height: 1.2; margin: .2rem 0 .3rem; }
.hero-copy { color: #405B70; font-size: 1rem; margin: 0; }
/* Loading screen: shown while Streamlit is recalculating (only if it takes longer than ~0.25 s) */
.gl-loader { display: none; position: fixed; inset: 0 0 auto 0; z-index: 1000000; pointer-events: none; }
[data-test-script-state="running"] .gl-loader { display: block; animation: gl-fade-in .2s ease .25s both; }
.gl-bar { height: 3px; background: linear-gradient(90deg, rgba(39,139,203,0) 0%, #278BCB 35%, #7CCBF7 50%, #278BCB 65%, rgba(39,139,203,0) 100%);
          background-size: 200% 100%; animation: gl-slide 1.1s linear infinite; }
.gl-card { position: fixed; top: 18px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: .65rem;
           padding: .6rem 1.15rem; background: #FFFFFF; border: 1px solid #A8D3EC; border-radius: 999px;
           box-shadow: 0 10px 28px rgba(39,94,132,.18); color: #17324A; font-weight: 650; font-size: .92rem; }
.gl-spin { width: 17px; height: 17px; border: 2.5px solid #D6ECFA; border-top-color: #278BCB; border-radius: 50%; animation: gl-rotate .7s linear infinite; }
[data-test-script-state="running"] [data-testid="stMain"] { cursor: progress; }
@keyframes gl-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes gl-slide { from { background-position: 100% 0; } to { background-position: -100% 0; } }
@keyframes gl-rotate { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .gl-bar, .gl-spin { animation-duration: 3s; } }
</style>
<div class="gl-loader" role="status" aria-live="polite"><div class="gl-bar"></div><div class="gl-card"><div class="gl-spin"></div>Loading data…</div></div>
""", unsafe_allow_html=True)


BOX = False  # True when the "Box office #1" data source is selected
BOX_TERMS = {
    "Reviews": "#1 weekends", "reviews": "#1 weekends", "Number of reviews": "Number of #1 weekends",
    "Distinct reviewers": "Countries", "Reviewers": "Countries", "Reviews submitted": "#1 weekends",
    "Rating year": "Year", "Rating month": "Month", "Rating submission year": "Year",
    "Ratings submitted between": "Box office weekends between",
    "Minimum reviews per group": "Minimum #1 weekends per group",
    "Most review activity": "Most #1 weekends", "Most distinct reviewers": "Most countries",
    "Avg. reviews per event window": "Avg. #1 weekends per event window",
    "Average reviews per annual window": "Avg. #1 weekends per annual window",
    "Avg. distinct reviewers per event window": "Avg. countries per event window",
    "Average distinct reviewers per window": "Avg. countries per window",
    "Avg. reviews per window": "Avg. #1 weekends per window", "Avg. reviewers per window": "Avg. countries per window",
    "Avg. reviews/window": "Avg. #1 weekends/window", "Avg. reviewers/window": "Avg. countries/window",
    "Avg. reviews per occurrence": "Avg. #1 weekends per occurrence",
    "Avg. reviews per occurrence of this date number": "Avg. #1 weekends per occurrence of this date number",
    "Avg. reviews per occurrence of that date": "Avg. #1 weekends per occurrence of that date",
    "Day of month when rating was submitted": "Day of month (weekend end date)",
    "Reviews by genre": "#1 weekends by genre",
}


def T(text: str) -> str:
    """Swap review wording for box office wording when the box office source is active."""
    return BOX_TERMS.get(text, text) if BOX else text


def _num(frame):
    """Box office rows have no score; keep average_rating numeric (NaN) so charts and maths never break."""
    if isinstance(frame, pd.DataFrame) and "average_rating" in frame.columns:
        frame = frame.copy()
        frame["average_rating"] = pd.to_numeric(frame["average_rating"], errors="coerce")
    return frame


@st.cache_data(show_spinner=False)
def cached_box_view(movielens_db: str, countries, scrape_mtime: float):
    return build_box_office_snapshot(movielens_db, tuple(countries))


@st.cache_data(show_spinner=False)
def cached_country_breakdown(db_path: str, genre, start: str, end: str, release_years, seasons):
    return country_breakdown(db_path, genre, start, end, release_years, seasons)


@st.cache_resource(show_spinner=False)
def cached_preload(db_path: str):
    preload(db_path)
    return True


@st.cache_data(show_spinner=False)
def cached_profile(db_path: str):
    return dataset_profile(db_path)


@st.cache_data(show_spinner=False)
def cached_summary(db_path: str, start: str, end: str, release_years, seasons, minimum_score):
    return _num(genre_summary(db_path, start, end, release_years, list(seasons), minimum_score))


@st.cache_data(show_spinner=False)
def cached_totals(db_path: str, start: str, end: str, release_years, seasons, minimum_score):
    return filtered_totals(db_path, start, end, release_years, list(seasons), minimum_score)


@st.cache_data(show_spinner=False)
def cached_trend(db_path: str, genre: str, start: str, end: str, release_years, seasons, minimum_score):
    return _num(genre_trend(db_path, genre, start, end, release_years, list(seasons), minimum_score))


@st.cache_data(show_spinner=False)
def cached_distribution(db_path: str, genre: str, start: str, end: str, release_years, seasons, minimum_score):
    return _num(rating_distribution(db_path, genre, start, end, release_years, list(seasons), minimum_score))


@st.cache_data(show_spinner=False)
def cached_monthly_activity(db_path: str, genre, start: str, end: str, release_years, months, minimum_score):
    return _num(monthly_activity(db_path, genre, start, end, release_years, list(months), minimum_score))


@st.cache_data(show_spinner=False)
def cached_monthly_history(db_path: str, genre, start: str, end: str, release_years, months, minimum_score):
    return _num(monthly_history(db_path, genre, start, end, release_years, list(months), minimum_score))


@st.cache_data(show_spinner=False)
def cached_weekday_activity(db_path: str, genre, start: str, end: str, release_years, months, minimum_score):
    return _num(weekday_activity(db_path, genre, start, end, release_years, list(months), minimum_score))


@st.cache_data(show_spinner=False)
def cached_day_activity(db_path: str, genre, start: str, end: str, release_years, months, minimum_score):
    return _num(day_of_month_activity(db_path, genre, start, end, release_years, list(months), minimum_score))


@st.cache_data(show_spinner=False)
def cached_special_days(db_path: str, genre, start: str, end: str, release_years, window_days, seasons, minimum_score):
    return _num(special_day_activity(db_path, genre, start, end, release_years, window_days, list(seasons), minimum_score))


@st.cache_data(show_spinner=False)
def cached_time_comparison(db_path: str, dimension: str, genre, start: str, end: str, release_years, seasons, minimum_score):
    return _num(time_comparison(db_path, dimension, genre, start, end, release_years, list(seasons), minimum_score))


def run_scrape(countries, first_year: int, last_year: int, refresh: bool):
    status = st.status("Scraping box office data from Wikipedia…", expanded=True)
    status.button("Cancel scraping", key="cancel_scrape", use_container_width=True,
                  on_click=lambda: st.session_state.update(scrape_cancelled=True),
                  help="Stops right away. Pages already downloaded stay saved; scraping again continues where it stopped.")
    bar = status.progress(0.0)
    note = status.empty()

    def update(text, fraction=None):
        note.caption(text)
        if fraction is not None:
            bar.progress(min(max(fraction, 0.0), 1.0))

    try:
        result = scrape_box_office(list(countries), first_year, last_year, refresh, update)
        status.update(label=f"Scrape finished: {result['weekends']:,} weekends from {result['pages']} pages", state="complete", expanded=False)
        if result["errors"]:
            st.warning(f"{result['errors']} pages could not be downloaded (network problem). Press the button again to retry them.")
        st.cache_data.clear()
        st.rerun()
    except Exception as error:
        status.update(label="Scraping stopped", state="error", expanded=True)
        st.error(f"Could not finish scraping: {error}. Check your internet connection and try again; pages already saved are kept.")


def import_dataset(name: str, source: str, movies_file, ratings_file):
    status = st.status("Creating a new database snapshot…", expanded=True)
    progress = st.empty()
    progress.caption("Reading the catalog and importing ratings…")
    last_value = 0

    def update_progress(count):
        nonlocal last_value
        last_value = count
        progress.caption(f"Imported {count:,} ratings…")

    try:
        db_path = create_snapshot(name, source, movies_file, ratings_file, update_progress)
        progress.success(f"Saved {last_value:,} ratings")
        status.update(label="Database snapshot is ready", state="complete", expanded=False)
        st.session_state["active_database"] = db_path
        st.cache_data.clear()
        st.rerun()
    except Exception as error:
        status.update(label="Could not import this dataset", state="error", expanded=True)
        st.error(str(error))


with st.sidebar:
    st.markdown("## Genre Lens")
    st.caption("A focused look at audience ratings by genre.")
    st.divider()

    snapshots = list_snapshots()
    snapshot_paths = [item["path"] for item in snapshots]
    active = st.session_state.get("active_database")
    if active not in snapshot_paths:
        active = snapshot_paths[0] if snapshot_paths else None
    if snapshots:
        labels = {item["path"]: f"{item['name']} · {item['imported']}" for item in snapshots}
        active = st.selectbox("Working database", snapshot_paths,
                              index=snapshot_paths.index(active), format_func=lambda path: labels[path])
        st.session_state["active_database"] = active
    else:
        st.info("Import a dataset to start exploring.")

    with st.expander("Add a dataset", expanded=not bool(snapshots)):
        st.markdown("For newer public data, download [MovieLens 32M](https://grouplens.org/datasets/movielens/32m/), unzip it, then import its folder below. It includes ratings through October 12, 2023.")
        sources = find_movie_datasets()
        if sources:
            source_options = {item["path"]: item for item in sources}
            selected_source = st.selectbox("Detected dataset folder", list(source_options),
                                           format_func=lambda path: f"{source_options[path]['name']} — {path}")
            if st.button("Import selected folder", use_container_width=True):
                selected = source_options[selected_source]
                try:
                    with open(Path(selected_source) / "movies.csv", "r", encoding="utf-8-sig", newline="") as movies_file, \
                         open(Path(selected_source) / "ratings.csv", "r", encoding="utf-8-sig", newline="") as ratings_file:
                        import_dataset(selected["name"], selected_source, movies_file, ratings_file)
                except OSError as error:
                    st.error(f"Could not open this dataset: {error}")
        else:
            st.info("No matching local folders found. Upload the two CSV files below.")

        st.markdown("**Or upload MovieLens CSVs**")
        uploaded_movies = st.file_uploader("movies.csv", type="csv", key="upload_movies")
        uploaded_ratings = st.file_uploader("ratings.csv", type="csv", key="upload_ratings")
        upload_name = st.text_input("Snapshot name", value="My dataset")
        if st.button("Import uploaded files", use_container_width=True,
                     disabled=not (uploaded_movies and uploaded_ratings)):
            import_dataset(upload_name.strip() or "My dataset", "Uploaded CSV files", uploaded_movies, uploaded_ratings)

    with st.expander("Scrape box office data", expanded=False):
        scrape_info = scrape_status()
        if scrape_info["weekends"]:
            st.caption(f"Saved: {scrape_info['weekends']:,} #1 weekends · {scrape_info['countries']} countries · "
                       f"{scrape_info['first']} → {scrape_info['last']} · last run {str(scrape_info['scraped_at']).replace('T', ' ')}")
        if st.session_state.pop("scrape_cancelled", False):
            st.info("Scraping cancelled. Everything downloaded so far is saved; press Scrape again to continue.")
        this_year = date.today().year
        quick_countries = ["United States", "United Kingdom", "China", "Japan", "South Korea", "France"]
        scrape_mode = st.radio("How much to scrape", ["Quick", "Full", "Custom"], horizontal=True, key="scrape_mode",
                               help="Quick: 6 big markets, last 10 years (well under a minute). Full: every country since 2000.")
        if scrape_mode == "Quick":
            scrape_countries, scrape_years = quick_countries, (this_year - 9, this_year)
            st.caption(f"{', '.join(quick_countries)} · {this_year - 9}–{this_year}")
        elif scrape_mode == "Full":
            scrape_countries, scrape_years = list(COUNTRIES), (2000, this_year)
            st.caption(f"All {len(COUNTRIES)} countries · 2000–{this_year}")
        else:
            scrape_countries = st.multiselect("Countries", list(COUNTRIES), default=quick_countries, key="scrape_countries",
                                              placeholder="Choose countries")
            scrape_years = st.slider("Years", 2000, this_year, (this_year - 9, this_year), key="scrape_years")
        scrape_refresh = st.checkbox("Re-download years already saved", value=False, key="scrape_refresh",
                                     help="Normally saved years are skipped and only the latest two years are refreshed.")
        if not SCRAPING_AVAILABLE:
            st.warning("Scraping needs two extra packages. Stop the app, run this in the same terminal, then start it again:")
            st.code(INSTALL_HINT, language="bash")
        if st.button("Scrape latest data", type="primary", use_container_width=True, disabled=not scrape_countries or not SCRAPING_AVAILABLE):
            run_scrape(scrape_countries, scrape_years[0], scrape_years[1], scrape_refresh)
        if scrape_info["weekends"] and st.checkbox("Show pages found per country", key="scrape_show_coverage"):
            st.dataframe(coverage_table(), hide_index=True, use_container_width=True)

st.markdown("""
<div class="hero-card">
  <div class="hero-eyebrow">MovieLens insights</div>
  <div class="hero-title">Genre Lens</div>
  <p class="hero-copy">Explore what people rate, how popular each genre is, and how reviews and box office hits change over time.</p>
</div>
""", unsafe_allow_html=True)

if not active:
    st.info("Choose a detected dataset folder or upload movies.csv and ratings.csv from the sidebar, then import it to create your first database snapshot.")
    st.stop()

for state_key in list(st.session_state.keys()):
    if str(state_key).startswith(("detail_", "compare_", "period_", "timing_", "special_")):
        st.session_state[state_key] = st.session_state[state_key]

movielens_db = active
source_labels = {"reviews": "Reviews (MovieLens)", "box": "Box office #1 (scraped)"}
source_columns = st.columns([1.25, 2])
with source_columns[0]:
    data_source = st.radio("Data source", list(source_labels), format_func=source_labels.get, horizontal=True,
                           key="data_source",
                           help="Reviews: MovieLens ratings. Box office #1: weekends when a film was the #1 film in a country, "
                                "scraped from Wikipedia. Every tab below uses the source chosen here.")
BOX = data_source == "box"
chosen_countries: list[str] = []
if BOX:
    available_countries = scraped_countries()
    if not available_countries:
        st.info("No box office data yet. Open **Scrape box office data** in the sidebar, press **Scrape latest data**, then choose this source again.")
        st.stop()
    with source_columns[1]:
        chosen_countries = st.multiselect("Box office countries", available_countries, default=[], key="box_countries",
                                          placeholder="Every scraped country (global)")
    box_view = cached_box_view(movielens_db, tuple(sorted(chosen_countries)), SCRAPE_DB.stat().st_mtime)
    if not box_view:
        st.warning("No scraped weekends for these countries yet. Pick other countries or scrape more data.")
        st.stop()
    active = box_view

with st.spinner("Loading dataset into memory…"):
    cached_preload(active)
profile = cached_profile(active)
db_meta = metadata(active)
if not profile["genres"] or not profile["first_date"]:
    st.error("This database has no usable movie genres or dated ratings. Import a MovieLens-compatible dataset to continue.")
    st.stop()

if BOX:
    coverage = float(db_meta.get("genre_coverage", "0") or 0)
    st.caption(f"{profile['reviewers']} countries · {profile['first_date']} → {profile['last_date']} · genres found for {coverage:.0%}")
else:
    st.caption(f"{db_meta.get('name', 'Dataset')} · {profile['first_date']} → {profile['last_date']}")

with st.container(border=True):
    st.markdown("#### Filters for every view")
    filter1, filter2, filter3, filter4, filter5 = st.columns([1.5, 1.25, 1, 1.2, 1.15])
    source_min = date.fromisoformat(profile["first_date"])
    source_max = date.fromisoformat(profile["last_date"])
    with filter1:
        activity_dates = st.date_input(T("Ratings submitted between"), value=(source_min, source_max),
                                       help="Filters by the weekend end date of each #1 weekend." if BOX else "Filters by the date a MovieLens user submitted a rating.",
                                       min_value=source_min, max_value=source_max, key=f"activity_dates_{data_source}")
    with filter2:
        release_range = st.slider("Film release years", int(profile["release_min"] or 1900),
                                  int(profile["release_max"] or 2026),
                                  (int(profile["release_min"] or 1900), int(profile["release_max"] or 2026)),
                                  help="Release year from Wikidata or the MovieLens catalog." if BOX else "Release year parsed from the catalog title.",
                                  key=f"release_years_{data_source}")
    with filter3:
        season_filter = st.multiselect("Seasons", ["Spring", "Summer", "Autumn", "Winter"],
                                       placeholder="Any season", key="shared_seasons",
                                       help="Leave empty to include ratings from every season.")
    with filter4:
        minimum_score = st.slider("Minimum score", min_value=0.0, max_value=5.0, value=0.0, step=0.5,
                                  help="Box office data has no score, so this filter only applies to reviews." if BOX else
                                       "Only include individual ratings at or above this score. Set to 0 to include all scores.",
                                  key="shared_minimum_score", disabled=BOX)
        if BOX:
            minimum_score = 0.0
    with filter5:
        minimum_ratings = st.number_input(T("Minimum reviews per group"), min_value=0, value=0, step=100,
                                          help="Hide time periods or genres with fewer rows than this.",
                                          key="shared_minimum_ratings")

if isinstance(activity_dates, (tuple, list)) and len(activity_dates) == 2:
    start_date, end_date = activity_dates
else:
    start_date = end_date = activity_dates
release_filter = tuple(release_range)

summary = cached_summary(active, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
totals = cached_totals(active, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
summary = summary[summary["rating_count"] >= minimum_ratings].copy() if not summary.empty else summary
if BOX and not summary.empty:
    summary = summary.sort_values("rating_count", ascending=False)
available_genres = summary["genre"].tolist() if not summary.empty else []
genre_color_map = {
    genre: GENRE_COLORS.get(genre, GENRE_PALETTE[index % len(GENRE_PALETTE)])
    for index, genre in enumerate(profile["genres"])
}

if totals["rating_count"] == 0:
    st.warning(f"No {T('reviews')} match these filters. Try a lower minimum score or widen the date or film-year range.")
    st.stop()

metric_cols = st.columns(5)
if BOX:
    metric_cols[0].metric("#1 films", f"{totals['film_count']:,}")
else:
    metric_cols[0].metric("Average rating", f"{totals['average_rating']:.2f} / 5")
metric_cols[1].metric(T("Reviews"), f"{totals['rating_count']:,}")
metric_cols[2].metric(T("Distinct reviewers"), f"{totals['reviewer_count']:,}")
metric_cols[3].metric("Release years", f"{totals['release_year_count']:,}")
metric_cols[4].metric("Genres in view", f"{len(summary):,}")
# on_change="rerun" makes tabs lazy: only the selected tab's charts are computed on each filter change.
overview_tab, detail_tab, compare_tab, period_compare_tab, timing_tab, special_tab = st.tabs(
    ["Overview", "Genre detail", "Compare genres", "Compare time periods", "Month patterns", "Special dates"],
    key="main_tab", on_change="rerun",
)

def share_by_year(db_path: str, genres, seasons, start: str, end: str, release_years, minimum_score: float) -> pd.DataFrame:
    """Each genre's share of all rows per year (reviews or #1 weekends), so sources of different size compare fairly."""
    yearly = cached_time_comparison(db_path, "Rating year", None, start, end, release_years, seasons, minimum_score)
    if yearly.empty:
        return pd.DataFrame(columns=["year", "genre", "share", "rating_count"])
    totals_by_year = yearly.set_index(yearly["time_group"].astype(int))["rating_count"]
    frames = []
    for genre_name in genres:
        trend_rows = cached_trend(db_path, genre_name, start, end, release_years, seasons, minimum_score)
        counts = trend_rows.set_index(trend_rows["year"].astype(int))["rating_count"] if not trend_rows.empty else pd.Series(dtype=float)
        counts = counts.reindex(totals_by_year.index, fill_value=0)   # years with no activity count as 0%, not a gap
        frames.append(pd.DataFrame({"year": counts.index, "genre": genre_name, "rating_count": counts.values,
                                    "share": counts.values / totals_by_year.values}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["year", "genre", "share", "rating_count"])


def cross_source_chart(genres, key: str):
    """Reviews vs. box office: a genre's share of activity per year in both sources on one chart."""
    st.markdown("##### Reviews vs. box office over time")
    if not genres:
        return
    if not scrape_status()["weekends"]:
        st.caption("Scrape box office data from the sidebar to compare review trends with box office #1 trends here.")
        return
    box_db = cached_box_view(movielens_db, tuple(sorted(chosen_countries)), SCRAPE_DB.stat().st_mtime)
    if not box_db:
        st.caption("No scraped box office weekends for the chosen countries.")
        return
    frames = []
    for source_name, db_path, score in (("Reviews", movielens_db, minimum_score if not BOX else 0.0), ("Box office #1", box_db, 0.0)):
        source_profile = cached_profile(db_path)
        if not source_profile["first_date"]:
            continue
        rows = share_by_year(db_path, genres, season_filter, source_profile["first_date"], source_profile["last_date"], None, score)
        if not rows.empty:
            frames.append(rows.assign(source=source_name))
    if not frames:
        st.info("No yearly data for these genres in either source.")
        return
    both = pd.concat(frames, ignore_index=True).sort_values(["genre", "source", "year"])
    cross_chart = px.line(both, x="year", y="share", color="genre", line_dash="source", markers=True,
                          color_discrete_map=genre_color_map,
                          line_dash_map={"Reviews": "solid", "Box office #1": "dash"},
                          hover_data={"share": ":.1%", "rating_count": ":,", "source": True},
                          labels={"year": "Year", "share": "Share of that year's activity", "genre": "Genre",
                                  "source": "Source", "rating_count": "Rows (reviews or #1 weekends)"})
    cross_chart.update_yaxes(tickformat=".0%", rangemode="tozero")
    cross_chart.update_layout(height=420, margin=dict(l=8, r=8, t=20, b=8), legend_title_text="")
    st.plotly_chart(cross_chart, use_container_width=True, theme=None, key=f"cross_{key}")
    st.caption("Solid = share of reviews · Dashed = share of box office #1 weekends")


with overview_tab:
    if overview_tab.open:
        st.subheader("Genre overview")
        if summary.empty:
            st.info(f"No genres meet the minimum {T('reviews')} setting. Lower it to see more results.")
        else:
            overview_measure = "rating_count" if BOX else "average_rating"
            view = summary.sort_values(overview_measure, ascending=True).copy()
            if BOX:
                view["share"] = view["rating_count"] / totals["rating_count"]
            chart_col, table_col = st.columns([1.4, 1])
            with chart_col:
                chart = px.bar(view, x=overview_measure, y="genre", orientation="h", color="genre",
                               color_discrete_map=genre_color_map,
                               hover_data={"rating_count": ":,", "reviewer_count": ":,", "film_count": ":,", "release_year_count": True}
                               if BOX else {"rating_count": ":,", "reviewer_count": ":,", "release_year_count": True},
                               labels={"average_rating": "Average rating (out of 5)", "genre": "Genre",
                                       "rating_count": T("Reviews"), "reviewer_count": T("Distinct reviewers"),
                                       "film_count": "#1 films", "release_year_count": "Release years"})
                chart.update_layout(height=max(430, 38 * len(view)), showlegend=False,
                                    margin=dict(l=8, r=15, t=28, b=10), yaxis_title=None)
                if BOX:
                    chart.update_xaxes(gridcolor="#E9EEF5", title="#1 weekends (a film with several genres counts for each)")
                else:
                    chart.update_xaxes(range=[0, 5], dtick=0.5, gridcolor="#E9EEF5")
                st.plotly_chart(chart, use_container_width=True, theme=None)
            with table_col:
                st.markdown("##### " + T("Reviews by genre"))
                table = summary.rename(columns={"genre": "Genre", "average_rating": "Avg rating",
                                                "rating_count": T("Reviews"), "reviewer_count": T("Reviewers"),
                                                "release_year_count": "Release years", "film_count": "#1 films" if BOX else "Titles"})
                if BOX:
                    table["Share of #1 weekends"] = (summary["rating_count"] / totals["rating_count"] * 100).round(1).astype(str) + "%"
                    table = table.sort_values(T("Reviews"), ascending=False)
                    table_columns = ["Genre", T("Reviews"), "Share of #1 weekends", "#1 films", T("Reviewers")]
                else:
                    table["Avg rating"] = table["Avg rating"].round(2)
                    table_columns = ["Genre", "Avg rating", "Reviews", "Reviewers", "Release years"]
                st.dataframe(table[table_columns], use_container_width=True, hide_index=True, height=560)
                st.download_button("Download genre summary (CSV)", table[table_columns].to_csv(index=False).encode("utf-8-sig"),
                                   "box_office_genre_summary.csv" if BOX else "genre_summary.csv", "text/csv", use_container_width=True)

with detail_tab:
    if detail_tab.open:
        st.subheader("Explore one genre")
        genre_options = available_genres or profile["genres"]
        genre = st.selectbox("Choose a genre", genre_options, key="detail_genre")
        detail = summary[summary["genre"] == genre]
        if detail.empty:
            st.info(f"This genre has no {T('reviews')} under the current filters. Try a lower minimum score or widen the date or film-year range.")
        else:
            row = detail.iloc[0]
            detail_metrics = st.columns(4)
            if BOX:
                detail_metrics[0].metric("#1 films", f"{int(row['film_count']):,}")
            else:
                detail_metrics[0].metric("Average rating", f"{row['average_rating']:.2f} / 5")
            detail_metrics[1].metric(T("Reviews"), f"{int(row['rating_count']):,}")
            detail_metrics[2].metric(T("Distinct reviewers"), f"{int(row['reviewer_count']):,}")
            detail_metrics[3].metric("Release years represented", f"{int(row['release_year_count']):,}")
            left, right = st.columns(2)
            with left:
                if BOX:
                    st.markdown("##### Share of #1 weekends by year")
                    trend = share_by_year(active, [genre], season_filter, start_date.isoformat(), end_date.isoformat(), release_filter, 0.0)
                    if not trend.empty:
                        trend["year"] = trend["year"].astype(str)
                        line = px.line(trend, x="year", y="share", markers=True, color_discrete_sequence=[genre_color_map.get(genre, PRIMARY_BLUE)],
                                       hover_data={"rating_count": ":,", "share": ":.1%"},
                                       labels={"year": "Year", "share": "Share of all #1 weekends", "rating_count": "#1 weekends"})
                        line.update_yaxes(tickformat=".0%", rangemode="tozero")
                        line.update_layout(height=380, margin=dict(l=8, r=8, t=20, b=8))
                        st.plotly_chart(line, use_container_width=True, theme=None)
                    else:
                        st.info("No trend data for this selection.")
                else:
                    st.markdown("##### Rating trend by year")
                    trend = cached_trend(active, genre, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
                    if not trend.empty:
                        trend["year"] = trend["year"].astype(str)
                        line = px.line(trend, x="year", y="average_rating", markers=True,
                                       hover_data={"rating_count": ":,", "reviewer_count": ":,"},
                                       labels={"year": "Rating year", "average_rating": "Average rating",
                                               "rating_count": "Reviews", "reviewer_count": "Reviewers"})
                        line.update_layout(height=380, yaxis_range=[0, 5], margin=dict(l=8, r=8, t=20, b=8))
                        st.plotly_chart(line, use_container_width=True, theme=None)
                    else:
                        st.info("No trend data for this selection.")
            with right:
                if BOX:
                    st.markdown("##### Where this genre reached #1")
                    countries_data = cached_country_breakdown(active, genre, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter)
                    if countries_data.empty:
                        st.info("No countries for this selection.")
                    else:
                        country_chart = px.bar(countries_data.sort_values("rating_count"), x="rating_count", y="country", orientation="h",
                                               hover_data={"film_count": True},
                                               labels={"rating_count": "#1 weekends", "country": "Country", "film_count": "#1 films"},
                                               color_discrete_sequence=[genre_color_map.get(genre, PRIMARY_BLUE)])
                        country_chart.update_layout(height=380, margin=dict(l=8, r=8, t=20, b=8))
                        st.plotly_chart(country_chart, use_container_width=True, theme=None)
                else:
                    st.markdown("##### Rating score distribution")
                    distribution = cached_distribution(active, genre, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
                    dist = px.bar(distribution, x="rating", y="rating_count", labels={"rating": "Score", "rating_count": "Reviews"},
                                  color_discrete_sequence=[PRIMARY_BLUE])
                    dist.update_layout(height=380, margin=dict(l=8, r=8, t=20, b=8))
                    dist.update_xaxes(dtick=0.5, gridcolor="#E9EEF5")
                    st.plotly_chart(dist, use_container_width=True, theme=None)
            cross_source_chart([genre], "detail")

with compare_tab:
    if compare_tab.open:
        st.subheader("Genre vs. genre")
        compared = st.multiselect("Choose genres", available_genres, default=available_genres[:2], key="compare_genres")
        compared_summary = summary[summary["genre"].isin(compared)].copy() if compared else pd.DataFrame()
        if len(compared) < 2:
            st.info("Choose at least two genres to create a comparison.")
        elif compared_summary.empty:
            st.info("No selected genres match the active filters.")
        else:
            compare_left, compare_right = st.columns(2)
            with compare_left:
                if BOX:
                    compared_summary["share"] = compared_summary["rating_count"] / totals["rating_count"]
                    avg_chart = px.bar(compared_summary.sort_values("share", ascending=False),
                                       x="genre", y="share", color="genre", color_discrete_map=genre_color_map, text_auto=".1%",
                                       labels={"genre": "Genre", "share": "Share of all #1 weekends"})
                    avg_chart.update_yaxes(tickformat=".0%")
                    avg_chart.update_layout(height=380, showlegend=False, margin=dict(l=8, r=8, t=25, b=8))
                else:
                    avg_chart = px.bar(compared_summary.sort_values("average_rating", ascending=False),
                                       x="genre", y="average_rating", color="genre", color_discrete_map=genre_color_map, text_auto=".2f",
                                       labels={"genre": "Genre", "average_rating": "Average rating"})
                    avg_chart.update_layout(height=380, showlegend=False, yaxis_range=[0, 5],
                                            margin=dict(l=8, r=8, t=25, b=8))
                st.plotly_chart(avg_chart, use_container_width=True, theme=None)
            with compare_right:
                reach_measures = ["rating_count", "film_count"] if BOX else ["rating_count", "reviewer_count"]
                reach_names = {"rating_count": T("Reviews"), "reviewer_count": T("Distinct reviewers"), "film_count": "#1 films"}
                reach = compared_summary.melt(id_vars="genre", value_vars=reach_measures, var_name="measure", value_name="count")
                reach["measure"] = reach["measure"].map(reach_names)
                reach_chart = px.bar(reach, x="genre", y="count", color="measure", barmode="group",
                                     color_discrete_sequence=[PRIMARY_BLUE, "#F28E2B"],
                                     labels={"genre": "Genre", "count": "Count", "measure": "Measure"})
                reach_chart.update_layout(height=380, margin=dict(l=8, r=8, t=25, b=8), legend_title=None)
                st.plotly_chart(reach_chart, use_container_width=True, theme=None)

            if BOX:
                yearly_share = share_by_year(active, compared, season_filter, start_date.isoformat(), end_date.isoformat(), release_filter, 0.0)
                if not yearly_share.empty:
                    st.markdown("##### Share of #1 weekends over time")
                    share_chart = px.line(yearly_share.sort_values(["genre", "year"]), x="year", y="share", color="genre", markers=True,
                                          color_discrete_map=genre_color_map, hover_data={"share": ":.1%", "rating_count": ":,"},
                                          labels={"year": "Year", "share": "Share of all #1 weekends", "genre": "Genre", "rating_count": "#1 weekends"})
                    share_chart.update_yaxes(tickformat=".0%", rangemode="tozero")
                    share_chart.update_xaxes(dtick=1)
                    share_chart.update_layout(height=420, margin=dict(l=8, r=8, t=25, b=8), legend_title_text="Genre")
                    st.plotly_chart(share_chart, use_container_width=True, theme=None)
            else:
                trend_frames = []
                for selected in compared:
                    current_trend = cached_trend(active, selected, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
                    if not current_trend.empty:
                        current_trend["genre"] = selected
                        trend_frames.append(current_trend)
                if trend_frames:
                    combined_trend = pd.concat(trend_frames, ignore_index=True)
                    combined_trend["year"] = combined_trend["year"].astype(int)
                    combined_trend = combined_trend.sort_values(["genre", "year"])
                    year_chart = go.Figure()
                    for genre_name in compared:
                        genre_trend_data = combined_trend[combined_trend["genre"] == genre_name]
                        if genre_trend_data.empty:
                            continue
                        year_chart.add_trace(go.Scatter(
                            x=genre_trend_data["year"],
                            y=genre_trend_data["average_rating"],
                            name=genre_name,
                            mode="lines+markers",
                            line={"color": genre_color_map[genre_name], "width": 2},
                            marker={"size": 7},
                            customdata=genre_trend_data[["rating_count", "reviewer_count"]],
                            hovertemplate=(
                                "Genre: %{fullData.name}<br>Year: %{x}<br>"
                                "Average rating: %{y:.2f}<br>Reviews: %{customdata[0]:,}<br>"
                                "Distinct reviewers: %{customdata[1]:,}<extra></extra>"
                            ),
                        ))
                    year_chart.update_layout(
                        height=420,
                        yaxis={"range": [0, 5], "title": "Average rating"},
                        xaxis={"title": "Rating submission year", "dtick": 1},
                        legend_title_text="Genre",
                        margin=dict(l=8, r=8, t=25, b=8),
                    )
                    st.markdown("##### Average score over time")
                    st.plotly_chart(year_chart, use_container_width=True, theme=None)
            cross_source_chart(compared, "compare")
            compare_table = compared_summary.rename(columns={"genre": "Genre", "average_rating": "Avg rating",
                                                             "rating_count": T("Reviews"), "reviewer_count": T("Reviewers"),
                                                             "release_year_count": "Release years", "film_count": "#1 films"})
            if BOX:
                compare_columns = ["Genre", T("Reviews"), "#1 films", T("Reviewers"), "Release years"]
            else:
                compare_table["Avg rating"] = compare_table["Avg rating"].round(2)
                compare_columns = ["Genre", "Avg rating", "Reviews", "Reviewers", "Release years"]
            st.dataframe(compare_table[compare_columns], use_container_width=True, hide_index=True)

with period_compare_tab:
    if period_compare_tab.open:
        st.subheader("Compare time periods")

        compare_filters = st.columns([1.35, 1.25, 1.15, 1.1])
        with compare_filters[0]:
            compare_dimension = st.selectbox("Compare by", ["Rating year", "Film release year", "Month", "Season", "Day of week", "Day of month", "Special date"],
                                             key="period_dimension", format_func=T)
        with compare_filters[1]:
            compare_genre_label = st.selectbox("Genre", ["All genres (no genre filter)", *available_genres], key="period_genre",
                                                help="Choose all genres together, or focus on one genre.")
            compare_genre = None if compare_genre_label == "All genres (no genre filter)" else compare_genre_label
        with compare_filters[2]:
            compare_measure_label = st.selectbox("Measure", ["Number of reviews", "Distinct reviewers"] if BOX else ["Average score", "Number of reviews", "Distinct reviewers"],
                                                 key=f"period_measure_{data_source}", format_func=T)
            compare_measure = {"Average score": "average_rating", "Number of reviews": "rating_count", "Distinct reviewers": "reviewer_count"}[compare_measure_label]
        with compare_filters[3]:
            if compare_dimension == "Special date":
                compare_window = st.slider("Days before/after event", min_value=0, max_value=14, value=3, key="period_window")
            else:
                compare_window = 3
        if compare_dimension == "Special date":
            period_data = cached_special_days(active, compare_genre, start_date.isoformat(), end_date.isoformat(), release_filter, compare_window, season_filter, minimum_score)
            period_data = period_data[period_data["rating_count"] >= minimum_ratings].copy()
            if not period_data.empty:
                period_data["rating_points"] = period_data["average_rating"] * period_data["rating_count"]
                period_data = period_data.groupby("event").agg(
                    rating_count=("rating_count", "mean"),
                    rating_points=("rating_points", "sum"),
                    total_ratings=("rating_count", "sum"),
                    reviewer_count=("reviewer_count", "mean"),
                    periods=("event_year", "nunique"),
                ).reset_index().rename(columns={"event": "time_group"})
                period_data["average_rating"] = period_data["rating_points"] / period_data["total_ratings"]
                group_measure_label = T(compare_measure_label)
        else:
            dimension_key = {"Rating year": "Rating year", "Film release year": "Release year",
                             "Month": "Month", "Season": "Season", "Day of week": "Weekday",
                             "Day of month": "Day of month"}[compare_dimension]
            period_data = cached_time_comparison(active, dimension_key, compare_genre,
                                                 start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
            period_data = period_data[period_data["rating_count"] >= minimum_ratings].copy()
            group_measure_label = T(compare_measure_label)
            if not period_data.empty:
                if compare_dimension == "Month":
                    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
                    period_data["time_group"] = period_data["time_group"].map(lambda month: month_names[int(month) - 1])
                elif compare_dimension == "Day of week":
                    weekday_names = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday"}
                    period_data["time_group"] = period_data["time_group"].map(weekday_names)
                else:
                    period_data["time_group"] = period_data["time_group"].astype(str)

        if period_data.empty:
            st.info("No groups match these filters. Widen the date range or lower the minimum reviews per group.")
        else:
            group_options = period_data["time_group"].dropna().astype(str).tolist()
            selected_groups = st.multiselect("Compare groups", group_options, default=[], placeholder="Every period",
                                             key=f"period_groups_{compare_dimension}",
                                             help="Leave empty to compare every available year, month, season, weekday, or special date.")
            if selected_groups:
                period_data = period_data[period_data["time_group"].astype(str).isin(selected_groups)].copy()
            if period_data.empty:
                st.info("No selected groups have data under the current filters.")
            else:
                active_measure = compare_measure
                chart_measure_label = group_measure_label
                if compare_dimension == "Day of month" and compare_measure == "rating_count":
                    active_measure = "average_per_occurrence"
                    chart_measure_label = T("Avg. reviews per occurrence of this date number")
                if compare_dimension == "Special date":
                    if compare_measure == "rating_count":
                        chart_measure_label = T("Avg. reviews per event window")
                    elif compare_measure == "reviewer_count":
                        chart_measure_label = T("Avg. distinct reviewers per event window")
                metric_values = period_data[active_measure]
                if compare_measure == "average_rating":
                    peak_value = period_data.loc[metric_values.idxmax()]
                    st.metric("Highest average score", f"{peak_value['time_group']} · {peak_value['average_rating']:.2f} / 5")
                elif compare_measure == "rating_count":
                    peak_value = period_data.loc[metric_values.idxmax()]
                    if compare_dimension == "Day of month":
                        st.metric("Highest average date activity", f"Day {peak_value['time_group']} · {peak_value['average_per_occurrence']:,.1f} / occurrence")
                    elif compare_dimension == "Special date":
                        st.metric(T("Most review activity"), f"{peak_value['time_group']} · {peak_value['rating_count']:,.0f} / window")
                    else:
                        st.metric(T("Most review activity"), f"{peak_value['time_group']} · {peak_value['rating_count']:,.0f}")
                else:
                    peak_value = period_data.loc[metric_values.idxmax()]
                    st.metric(T("Most distinct reviewers"), f"{peak_value['time_group']} · {peak_value['reviewer_count']:,.0f}")

                chart_data = period_data.copy()
                if compare_dimension in {"Rating year", "Film release year"}:
                    chart_data["sort_key"] = pd.to_numeric(chart_data["time_group"], errors="coerce")
                    chart_data = chart_data.sort_values("sort_key")
                    period_chart = px.line(chart_data, x="time_group", y=active_measure, markers=True,
                                           labels={"time_group": T(compare_dimension), active_measure: chart_measure_label,
                                                   "rating_count": T("Reviews"), "reviewer_count": T("Reviewers")},
                                           hover_data={"rating_count": ":,", "reviewer_count": ":,"})
                else:
                    chart_data = chart_data.sort_values(active_measure, ascending=True)
                    period_chart = px.bar(chart_data, x=active_measure, y="time_group", orientation="h",
                                          color=active_measure, color_continuous_scale=[[0, "#DDF1FF"], [1, PRIMARY_BLUE]],
                                          labels={"time_group": T(compare_dimension), active_measure: chart_measure_label,
                                                  "rating_count": T("Reviews"), "reviewer_count": T("Reviewers")},
                                          hover_data={"rating_count": ":,", "reviewer_count": ":,"})
                period_chart.update_layout(height=max(360, min(650, 36 * len(chart_data) + 100)),
                                           coloraxis_showscale=False, margin=dict(l=8, r=8, t=20, b=8))
                if compare_measure == "average_rating":
                    if compare_dimension in {"Rating year", "Film release year"}:
                        period_chart.update_yaxes(range=[0, 5])
                    else:
                        period_chart.update_xaxes(range=[0, 5], dtick=0.5)
                st.plotly_chart(period_chart, use_container_width=True, theme=None)

                table_columns = ["time_group", "rating_count", "reviewer_count"] if BOX else ["time_group", "average_rating", "rating_count", "reviewer_count"]
                if "periods" in period_data.columns:
                    table_columns.append("periods")
                if compare_dimension == "Day of month":
                    table_columns.extend(["average_per_occurrence", "date_occurrences"])
                table_rating_label = T("Avg. reviews per event window") if compare_dimension == "Special date" else T("Reviews")
                table = period_data[table_columns].copy().rename(columns={"time_group": "Compared period",
                    "average_rating": "Avg. score", "rating_count": table_rating_label,
                    "reviewer_count": T("Reviewers"), "periods": "Years with data",
                    "average_per_occurrence": T("Avg. reviews per occurrence"), "date_occurrences": "Months represented"})
                if "Avg. score" in table.columns:
                    table["Avg. score"] = table["Avg. score"].round(2)
                st.dataframe(table, use_container_width=True, hide_index=True)

with timing_tab:
    if timing_tab.open:
        st.subheader("Month-by-month box office #1 activity" if BOX else "Month-by-month audience activity")

        with st.expander("Chart options", expanded=False):
            timing_filters = st.columns([1, 1])
            with timing_filters[0]:
                timing_genre_label = st.selectbox("Genre", ["All genres (no genre filter)", *available_genres], key="timing_genre",
                                                   help="Choose all genres together, or focus on one genre.")
                timing_genre = None if timing_genre_label == "All genres (no genre filter)" else timing_genre_label
            with timing_filters[1]:
                metric_label = st.selectbox("Measure", ["Number of reviews", "Distinct reviewers"] if BOX else ["Number of reviews", "Distinct reviewers", "Average score"],
                                            key=f"timing_metric_{data_source}", format_func=T)
                metric_column = {"Number of reviews": "rating_count", "Distinct reviewers": "reviewer_count", "Average score": "average_rating"}[metric_label]
                metric_axis = T({"Number of reviews": "Reviews submitted", "Distinct reviewers": "Distinct reviewers", "Average score": "Average score (out of 5)"}[metric_label])
        season_months = {"Spring": [3, 4, 5], "Summer": [6, 7, 8], "Autumn": [9, 10, 11], "Winter": [12, 1, 2]}
        selected_months = sorted({month for season in season_filter for month in season_months[season]})

        monthly = cached_monthly_activity(active, timing_genre, start_date.isoformat(), end_date.isoformat(), release_filter, tuple(selected_months), minimum_score)
        monthly = monthly[monthly["rating_count"] >= minimum_ratings].copy()
        history = cached_monthly_history(active, timing_genre, start_date.isoformat(), end_date.isoformat(), release_filter, tuple(selected_months), minimum_score)
        history = history[history["rating_count"] >= minimum_ratings].copy()
        weekdays = cached_weekday_activity(active, timing_genre, start_date.isoformat(), end_date.isoformat(), release_filter, tuple(selected_months), minimum_score)
        weekdays = weekdays[weekdays["rating_count"] >= minimum_ratings].copy()
        day_numbers = cached_day_activity(active, timing_genre, start_date.isoformat(), end_date.isoformat(), release_filter, tuple(selected_months), minimum_score)
        day_numbers = day_numbers[day_numbers["rating_count"] >= minimum_ratings].copy()

        if monthly.empty:
            st.info("No monthly activity matches these filters. Widen the date range or lower the minimum reviews per group.")
        else:
            month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            monthly["month_name"] = monthly["month"].map(lambda month: month_names[int(month) - 1])
            best_month = monthly.loc[monthly["rating_count"].idxmax()]
            month_metrics = st.columns(3)
            month_metrics[0].metric(T("Most review activity"), f"{best_month['month_name']} · {best_month['rating_count']:,}")
            if BOX:
                quiet_month = monthly.loc[monthly["rating_count"].idxmin()]
                month_metrics[1].metric("Fewest #1 weekends", f"{quiet_month['month_name']} · {quiet_month['rating_count']:,}")
            else:
                best_score_month = monthly.loc[monthly["average_rating"].idxmax()]
                month_metrics[1].metric("Highest average score", f"{best_score_month['month_name']} · {best_score_month['average_rating']:.2f} / 5")
            month_metrics[2].metric("Months represented", f"{monthly['month'].nunique()} / 12")

            st.markdown("##### Month × year map")
            if not history.empty:
                history["month_num"] = pd.to_datetime(history["year_month"] + "-01").dt.month
                history["year"] = pd.to_datetime(history["year_month"] + "-01").dt.year
                month_grid = history.pivot(index="year", columns="month_num", values=metric_column).reindex(columns=range(1, 13))
                month_grid = month_grid.iloc[::-1]
                heatmap_format = ",.2f" if metric_column == "average_rating" else ",.0f"
                heatmap = px.imshow(month_grid, x=month_names, y=[str(year) for year in month_grid.index],
                                    aspect="auto", color_continuous_scale="Blues",
                                    labels={"x": "Month", "y": T("Rating year"), "color": metric_axis})
                heatmap.update_traces(hovertemplate="Year: %{y}<br>Month: %{x}<br>" + metric_axis + ": %{z:" + heatmap_format + "}<extra></extra>")
                heatmap.update_layout(height=max(330, min(620, 22 * len(month_grid) + 100)),
                                      margin=dict(l=8, r=8, t=18, b=8))
                if metric_column == "average_rating":
                    heatmap.update_coloraxes(cmin=0, cmax=5)
                st.plotly_chart(heatmap, use_container_width=True, theme=None)
            else:
                st.info("No year-month cells meet this minimum activity threshold.")

            st.markdown("##### Which months have the most activity?")
            month_chart = px.bar(monthly.sort_values("month"), x="month_name", y=metric_column,
                                 hover_data={"rating_count": ":,", "reviewer_count": ":,"} if BOX else {"rating_count": ":,", "reviewer_count": ":,", "average_rating": ":.2f"},
                                 labels={"month_name": T("Rating month"), metric_column: metric_axis,
                                         "rating_count": T("Reviews"), "reviewer_count": T("Reviewers"),
                                         "average_rating": "Average score"}, color_discrete_sequence=[PRIMARY_BLUE])
            month_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
            if metric_column == "average_rating":
                month_chart.update_yaxes(range=[0, 5])
            st.plotly_chart(month_chart, use_container_width=True, theme=None)

            st.markdown("##### Which dates hold the most #1 weekends" if BOX else "##### When people submit the most reviews")
            timing_left, timing_right = st.columns(2)
            if BOX:
                timing_right, timing_left = st.container(), st.empty()
            if not weekdays.empty and not BOX:
                peak_weekday = weekdays.loc[weekdays["rating_count"].idxmax()]
                timing_left.metric("Most active review weekday", f"{peak_weekday['weekday_name']} · {peak_weekday['rating_count']:,}")
                weekday_chart = px.bar(weekdays, x="weekday_name", y=metric_column,
                                       category_orders={"weekday_name": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
                                       labels={"weekday_name": "Rating submitted on", metric_column: metric_axis},
                                       color_discrete_sequence=[PRIMARY_BLUE])
                weekday_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
                if metric_column == "average_rating":
                    weekday_chart.update_yaxes(range=[0, 5])
                timing_left.plotly_chart(weekday_chart, use_container_width=True, theme=None)
            if not day_numbers.empty:
                day_metric = "average_per_occurrence" if metric_column == "rating_count" else metric_column
                day_axis = T("Avg. reviews per occurrence of that date") if metric_column == "rating_count" else metric_axis
                peak_day = day_numbers.loc[day_numbers[day_metric].idxmax()]
                peak_value = peak_day["average_per_occurrence"] if metric_column == "rating_count" else peak_day[metric_column]
                timing_right.metric("Most active calendar date", f"Day {int(peak_day['day_of_month'])} · {peak_value:,.1f}")
                day_chart = px.bar(day_numbers, x="day_of_month", y=day_metric,
                                   hover_data={"occurrence_count": True, "rating_count": ":,"} if BOX else {"occurrence_count": True, "rating_count": ":,", "average_rating": ":.2f"},
                                   labels={"day_of_month": T("Day of month when rating was submitted"), day_metric: day_axis,
                                            "occurrence_count": "Months represented", "rating_count": T("Reviews"),
                                           "average_rating": "Average score"},
                                   color_discrete_sequence=[PRIMARY_BLUE])
                day_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
                day_chart.update_xaxes(dtick=1)
                if metric_column == "average_rating":
                    day_chart.update_yaxes(range=[0, 5])
                timing_right.plotly_chart(day_chart, use_container_width=True, theme=None)
with special_tab:
    if special_tab.open:
        st.subheader("Special dates & holiday periods")

        special_order = SPECIAL_DATE_ORDER
        with st.expander("Special date filters", expanded=True):
            date_options = st.columns([1.4, 1])
            with date_options[0]:
                chosen_events = st.multiselect("Dates to compare", special_order, default=[],
                                               placeholder="Every special date", key="special_event_compare",
                                               help="Leave empty to include all dates in the list.")
            with date_options[1]:
                window_days = st.slider("Event window (days before and after)", 0, 14, 3, key="special_window")

        with st.expander("Chart options", expanded=False):
            special_options = st.columns([1.4, 1])
            with special_options[0]:
                default_genres = available_genres[:2]
                special_genres = st.multiselect("Genres to compare", available_genres, default=default_genres,
                                                placeholder="Select one or more genres", key="special_genres",
                                                help="Each selected genre appears as its own series in the chart.")
            with special_options[1]:
                special_measure_label = st.selectbox("Chart measure", ["Number of reviews", "Distinct reviewers"] if BOX else ["Average score", "Number of reviews", "Distinct reviewers"],
                                                     format_func=T,
                                                     key=f"special_measure_{data_source}")

        genre_frames = []
        for selected_genre in special_genres:
            genre_data = cached_special_days(active, selected_genre, start_date.isoformat(), end_date.isoformat(), release_filter, window_days, season_filter, minimum_score)
            if not genre_data.empty:
                genre_data["genre"] = selected_genre
                genre_frames.append(genre_data)
        event_rows = pd.concat(genre_frames, ignore_index=True) if genre_frames else pd.DataFrame()
        if not event_rows.empty:
            event_rows = event_rows[event_rows["rating_count"] >= minimum_ratings].copy()
            if chosen_events:
                event_rows = event_rows[event_rows["event"].isin(chosen_events)].copy()
        if event_rows.empty:
            st.info("Choose at least one genre with special-date data, or widen the shared date and release-year filters.")
        else:
            event_options = sorted(event_rows["event"].unique(), key=lambda item: special_order.index(item) if item in special_order else len(special_order))

            event_rows["rating_points"] = event_rows["average_rating"] * event_rows["rating_count"]
            event_summary = event_rows.groupby(["event", "genre"]).agg(
                rating_count=("rating_count", "mean"),
                rating_points=("rating_points", "sum"),
                total_ratings=("rating_count", "sum"),
                reviewer_count=("reviewer_count", "mean"),
                event_years=("event_year", "nunique"),
            ).reset_index()
            event_summary["average_rating"] = event_summary["rating_points"] / event_summary["total_ratings"]
            event_summary["event_order"] = event_summary["event"].map({event: position for position, event in enumerate(special_order)})
            event_summary = event_summary.sort_values("event_order")

            measure_config = {
                "Average score": ("average_rating", "Average score (out of 5)"),
                "Number of reviews": ("rating_count", "Average reviews per annual window"),
                "Distinct reviewers": ("reviewer_count", "Average distinct reviewers per window"),
            }
            measure_column, measure_axis = measure_config[special_measure_label]
            measure_axis = T(measure_axis)
            sort_order = event_summary.groupby("event")[measure_column].mean().sort_values().index
            special_chart = px.bar(event_summary, x=measure_column, y="event", color="genre", color_discrete_map=genre_color_map, barmode="group",
                                   orientation="h", category_orders={"event": list(sort_order)},
                                   hover_data=({} if BOX else {"average_rating": ":.2f"}) | {"rating_count": ":.1f", "reviewer_count": ":.1f", "event_years": True},
                                   labels={"event": "Special date", "genre": "Genre", measure_column: measure_axis,
                                            "average_rating": "Avg. score", "rating_count": T("Avg. reviews/window"),
                                           "reviewer_count": T("Avg. reviewers/window"), "event_years": "Years with data"})
            special_chart.update_layout(height=max(380, 42 * len(event_options)), margin=dict(l=8, r=8, t=24, b=8),
                                        legend_title_text="Genre")
            if measure_column == "average_rating":
                special_chart.update_xaxes(range=[0, 5], dtick=0.5)
            st.plotly_chart(special_chart, use_container_width=True, theme=None)

            event_table = event_summary[["event", "genre", "average_rating", "rating_count", "reviewer_count", "event_years"]].copy()
            event_table.columns = ["Special date", "Genre", "Avg. score", T("Avg. reviews per window"), T("Avg. reviewers per window"), "Years with data"]
            for column in ["Avg. score", T("Avg. reviews per window"), T("Avg. reviewers per window")]:
                event_table[column] = event_table[column].round(2 if column == "Avg. score" else 1)
            if BOX:
                event_table = event_table.drop(columns=["Avg. score"])
            st.dataframe(event_table, use_container_width=True, hide_index=True)

            st.markdown("##### How one special date changes over time")
            history_columns = st.columns([1.2, 1])
            with history_columns[0]:
                history_event = st.selectbox("Track a date", event_options, key="special_history_event")
            with history_columns[1]:
                history_measure = st.selectbox("Yearly measure", ["Number of reviews", "Distinct reviewers"] if BOX else ["Average score", "Number of reviews", "Distinct reviewers"],
                                               key=f"special_history_measure_{data_source}", format_func=T)
            history_column = {"Number of reviews": "rating_count", "Average score": "average_rating",
                              "Distinct reviewers": "reviewer_count"}[history_measure]
            event_history = event_rows[event_rows["event"] == history_event].sort_values("event_year")
            history_chart = px.line(event_history, x="event_year", y=history_column, color="genre", color_discrete_map=genre_color_map, markers=True,
                                    hover_data=({"rating_count": ":,", "reviewer_count": ":,"} if BOX else {"rating_count": ":,", "average_rating": ":.2f", "reviewer_count": ":,"}),
                                    labels={"event_year": "Year", history_column: T(history_measure),
                                            "rating_count": T("Reviews"), "average_rating": "Average score",
                                            "reviewer_count": T("Distinct reviewers"), "genre": "Genre"})
            history_chart.update_layout(height=360, margin=dict(l=8, r=8, t=20, b=8), legend_title_text="Genre")
            if history_column == "average_rating":
                history_chart.update_yaxes(range=[0, 5])
            st.plotly_chart(history_chart, use_container_width=True, theme=None)
