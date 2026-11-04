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
    FIRST_YEAR,
    INSTALL_HINT,
    SCRAPE_DB,
    SCRAPING_AVAILABLE,
    build_review_view,
    coverage_table,
    scrape_reviews,
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


def remembered(key: str, options, fallback):
    """Default for a tab widget: None when a remembered choice exists (dropping options no longer available)."""
    if key in st.session_state:
        saved = st.session_state[key] or []
        valid = [value for value in saved if value in options]
        if saved and not valid:              # nothing remembered exists in this data: start from the default again
            del st.session_state[key]
            return fallback
        st.session_state[key] = valid
        return None
    return fallback


def _num(frame):
    """Keep average_rating numeric (NaN instead of None for empty groups) so charts and maths never break."""
    if isinstance(frame, pd.DataFrame) and "average_rating" in frame.columns:
        frame = frame.copy()
        frame["average_rating"] = pd.to_numeric(frame["average_rating"], errors="coerce")
    return frame


@st.cache_data(show_spinner=False)
def cached_data_view(movielens_db: str, include_mine: bool, scrape_mtime: float):
    """Database for "Scraped reviews" (include_mine=False) or "Both" (include_mine=True)."""
    return build_review_view(movielens_db, include_mine)


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


def run_scrape(first_year: int, last_year: int, max_films: int, refresh: bool):
    status = st.status("Scraping critic reviews…", expanded=True)
    status.button("Cancel scraping", key="cancel_scrape", width="stretch",
                  on_click=lambda: st.session_state.update(scrape_cancelled=True),
                  help="Stops right away. Everything already downloaded stays saved; scraping again continues where it stopped.")
    bar = status.progress(0.0)
    note = status.empty()

    def update(text, fraction=None):
        note.caption(text)
        if fraction is not None:
            bar.progress(min(max(fraction, 0.0), 1.0))

    try:
        result = scrape_reviews(first_year, last_year, max_films, refresh, update)
        status.update(label=f"Done: {result['looked_up']:,} films checked, {result['with_scores']:,} with critic scores",
                      state="complete", expanded=False)
        if result["errors"]:
            st.warning(f"{result['errors']} pages could not be downloaded (network problem). Press the button again to retry them.")
        st.cache_data.clear()
        st.rerun()
    except Exception as error:
        status.update(label="Scraping stopped", state="error", expanded=True)
        st.error(f"Could not finish scraping: {error}. Check your internet connection and try again; work already saved is kept.")


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
            if st.button("Import selected folder", width="stretch"):
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
        if st.button("Import uploaded files", width="stretch",
                     disabled=not (uploaded_movies and uploaded_ratings)):
            import_dataset(upload_name.strip() or "My dataset", "Uploaded CSV files", uploaded_movies, uploaded_ratings)

    with st.expander("Scrape critic reviews", expanded=False):
        scrape_info = scrape_status()
        if scrape_info["films"]:
            st.caption(f"Saved: {scrape_info['reviews']:,} critic reviews · {scrape_info['films']:,} films · "
                       f"{scrape_info['first']} → {scrape_info['last']}"
                       + (f" · {scrape_info['pending']:,} films still to check" if scrape_info["pending"] else ""))
        if st.session_state.pop("scrape_cancelled", False):
            st.info("Scraping cancelled. Everything downloaded so far is saved; press Scrape again to continue.")
        this_year = date.today().year
        scrape_years = st.slider("Release years", FIRST_YEAR, this_year, (FIRST_YEAR, this_year), key="scrape_years")
        max_films = st.slider("Max films per run", 50, 1000, 300, step=50, key="scrape_max_films",
                              help="Newest films are checked first. Each run only looks up films it has not checked before, "
                                   "so a smaller number keeps each run short.")
        scrape_refresh = st.checkbox("Re-read film lists already saved", value=False, key="scrape_refresh",
                                     help="Normally saved years are skipped and only the latest two years are re-read.")
        if not SCRAPING_AVAILABLE:
            st.warning("Scraping needs two extra packages. Stop the app, run this in the same terminal, then start it again:")
            st.code(INSTALL_HINT, language="bash")
        if st.button("Scrape latest data", type="primary", width="stretch", disabled=not SCRAPING_AVAILABLE):
            run_scrape(scrape_years[0], scrape_years[1], max_films, scrape_refresh)
        if scrape_info["films"] and st.checkbox("Show films per year", key="scrape_show_coverage"):
            st.dataframe(coverage_table(), hide_index=True, width="stretch")

st.markdown("""
<div class="hero-card">
  <div class="hero-title">Genre Lens</div>
  <p class="hero-copy"></p>
</div>
""", unsafe_allow_html=True)

if not active:
    st.info("Choose a detected dataset folder or upload movies.csv and ratings.csv from the sidebar, then import it to create your first database snapshot.")
    st.stop()

for state_key in list(st.session_state.keys()):
    if str(state_key).startswith(("detail_", "compare_", "period_", "timing_", "special_")):
        st.session_state[state_key] = st.session_state[state_key]

movielens_db = active
data_labels = {"mine": "File Data", "scraped": "Scraped reviews", "both": "Full dataset"}
data_choice = st.radio("Data", list(data_labels), format_func=data_labels.get, horizontal=True, key="data_choice",
                       help="Your data: the MovieLens snapshot chosen in the sidebar. Scraped reviews: critic scores scraped "
                            "from Wikipedia and Wikidata. Both: the two added together into one dataset.")
if data_choice != "mine":
    if not scrape_status()["reviews"]:
        st.info("No scraped reviews yet. Open **Scrape critic reviews** in the sidebar and press **Scrape latest data**.")
        st.stop()
    active = cached_data_view(movielens_db, data_choice == "both", SCRAPE_DB.stat().st_mtime)
with st.spinner("Loading dataset into memory…"):
    cached_preload(active)
profile = cached_profile(active)
db_meta = metadata(active)
if not profile["genres"] or not profile["first_date"]:
    st.error("This database has no usable movie genres or dated ratings. Import a MovieLens-compatible dataset to continue.")
    st.stop()

st.caption(f"{db_meta.get('name', 'Dataset')} · {profile['first_date']} → {profile['last_date']}")

with st.container(border=True):
    st.markdown("#### Filters for every view")
    filter1, filter2, filter3, filter4, filter5 = st.columns([1.5, 1.25, 1, 1.2, 1.15])
    source_min = date.fromisoformat(profile["first_date"])
    source_max = date.fromisoformat(profile["last_date"])
    with filter1:
        activity_dates = st.date_input("Ratings submitted between", value=(source_min, source_max),
                                       help="Filters by the review date (for scraped critic reviews: the film's release date).",
                                       min_value=source_min, max_value=source_max, key=f"activity_dates_{data_choice}")
    with filter2:
        first_release = int(profile["release_min"] or 1900)
        last_release = int(profile["release_max"] or first_release)
        if first_release < last_release:
            release_range = st.slider("Film release years", first_release, last_release, (first_release, last_release),
                                      help="Release year of the film.", key=f"release_years_{data_choice}")
        else:                                # only one release year in this data: nothing to slide between
            st.markdown(f"**Film release years**  \n{first_release}")
            release_range = (first_release, last_release)
    with filter3:
        season_filter = st.multiselect("Seasons", ["Spring", "Summer", "Autumn", "Winter"],
                                       placeholder="Any season", key="shared_seasons",
                                       help="Leave empty to include ratings from every season.")
    with filter4:
        minimum_score = st.slider("Minimum score", min_value=0.0, max_value=5.0, value=0.0, step=0.5,
                                  help="Only include individual ratings at or above this score. Set to 0 to include all scores.",
                                  key="shared_minimum_score")
    with filter5:
        minimum_ratings = st.number_input("Minimum reviews per group", min_value=0, value=0, step=100,
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
available_genres = summary["genre"].tolist() if not summary.empty else []
genre_color_map = {
    genre: GENRE_COLORS.get(genre, GENRE_PALETTE[index % len(GENRE_PALETTE)])
    for index, genre in enumerate(profile["genres"])
}

if totals["rating_count"] == 0:
    st.warning(f"No {'reviews'} match these filters. Try a lower minimum score or widen the date or film-year range.")
    st.stop()

metric_cols = st.columns(5)
metric_cols[0].metric("Average rating", f"{totals['average_rating']:.2f} / 5")
metric_cols[1].metric("Reviews", f"{totals['rating_count']:,}")
metric_cols[2].metric("Distinct reviewers", f"{totals['reviewer_count']:,}")
metric_cols[3].metric("Release years", f"{totals['release_year_count']:,}")
metric_cols[4].metric("Genres in view", f"{len(summary):,}")
# on_change="rerun" makes tabs lazy: only the selected tab's charts are computed on each filter change.
overview_tab, detail_tab, compare_tab, period_compare_tab, timing_tab, special_tab = st.tabs(
    ["Overview", "Genre detail", "Compare genres", "Compare time periods", "Month patterns", "Special dates"],
    key="main_tab", on_change="rerun",
)

def source_comparison_chart(genres, key: str):
    """Shown with "Both": average score per year in your data (solid) and in the scraped reviews (dashed)."""
    if data_choice != "both" or not genres:
        return
    scraped_db = cached_data_view(movielens_db, False, SCRAPE_DB.stat().st_mtime)
    frames = []
    for source_name, db_path in (("Your data", movielens_db), ("Scraped reviews", scraped_db)):
        for genre_name in genres:
            rows = cached_trend(db_path, genre_name, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
            if not rows.empty:
                frames.append(rows.assign(genre=genre_name, source=source_name, year=rows["year"].astype(int)))
    if not frames:
        return
    st.markdown("##### Your data vs. scraped reviews")
    both = pd.concat(frames, ignore_index=True).sort_values(["genre", "source", "year"])
    chart = px.line(both, x="year", y="average_rating", color="genre", line_dash="source", markers=True,
                    color_discrete_map=genre_color_map, line_dash_map={"Your data": "solid", "Scraped reviews": "dash"},
                    hover_data={"average_rating": ":.2f", "rating_count": ":,", "source": True},
                    labels={"year": "Year", "average_rating": "Average score", "genre": "Genre", "source": "Data",
                            "rating_count": "Reviews"})
    chart.update_yaxes(range=[0, 5])
    chart.update_layout(height=400, margin=dict(l=8, r=8, t=20, b=8), legend_title_text="")
    st.plotly_chart(chart, width="stretch", theme=None, key=f"sources_{key}")
    st.caption("Solid = your data · Dashed = scraped critic reviews")


with overview_tab:
    if overview_tab.open:
        st.subheader("Genre overview")
        if summary.empty:
            st.info(f"No genres meet the minimum reviews setting. Lower it to see more results.")
        else:
            overview_measure = "average_rating"
            view = summary.sort_values(overview_measure, ascending=True).copy()
            chart_col, table_col = st.columns([1.4, 1])
            with chart_col:
                chart = px.bar(view, x=overview_measure, y="genre", orientation="h", color="genre",
                               color_discrete_map=genre_color_map,
                               hover_data={"rating_count": ":,", "reviewer_count": ":,", "release_year_count": True},
                               labels={"average_rating": "Average rating (out of 5)", "genre": "Genre",
                                       "rating_count": "Reviews", "reviewer_count": "Distinct reviewers",
                                       "release_year_count": "Release years"})
                chart.update_layout(height=max(430, 38 * len(view)), showlegend=False,
                                    margin=dict(l=8, r=15, t=28, b=10), yaxis_title=None)
                chart.update_xaxes(range=[0, 5], dtick=0.5, gridcolor="#E9EEF5")
                st.plotly_chart(chart, width="stretch", theme=None)
            with table_col:
                st.markdown("##### Reviews by genre")
                table = summary.rename(columns={"genre": "Genre", "average_rating": "Avg rating",
                                                "rating_count": "Reviews", "reviewer_count": "Reviewers",
                                                "release_year_count": "Release years", "film_count": "Titles"})
                table["Avg rating"] = table["Avg rating"].round(2)
                table_columns = ["Genre", "Avg rating", "Reviews", "Reviewers", "Release years"]
                st.dataframe(table[table_columns], width="stretch", hide_index=True, height=560)
                st.download_button("Download genre summary (CSV)", table[table_columns].to_csv(index=False).encode("utf-8-sig"),
                                   "genre_summary.csv", "text/csv", width="stretch")

with detail_tab:
    if detail_tab.open:
        st.subheader("Explore one genre")
        genre_options = available_genres or profile["genres"]
        genre = st.selectbox("Choose a genre", genre_options, key="detail_genre")
        detail = summary[summary["genre"] == genre]
        if detail.empty:
            st.info(f"This genre has no reviews under the current filters. Try a lower minimum score or widen the date or film-year range.")
        else:
            row = detail.iloc[0]
            detail_metrics = st.columns(4)
            detail_metrics[0].metric("Average rating", f"{row['average_rating']:.2f} / 5")
            detail_metrics[1].metric("Reviews", f"{int(row['rating_count']):,}")
            detail_metrics[2].metric("Distinct reviewers", f"{int(row['reviewer_count']):,}")
            detail_metrics[3].metric("Release years represented", f"{int(row['release_year_count']):,}")
            left, right = st.columns(2)
            with left:
                st.markdown("##### Rating trend by year")
                trend = cached_trend(active, genre, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
                if not trend.empty:
                    trend["year"] = trend["year"].astype(str)
                    line = px.line(trend, x="year", y="average_rating", markers=True,
                                   hover_data={"rating_count": ":,", "reviewer_count": ":,"},
                                   labels={"year": "Rating year", "average_rating": "Average rating",
                                           "rating_count": "Reviews", "reviewer_count": "Reviewers"})
                    line.update_layout(height=380, yaxis_range=[0, 5], margin=dict(l=8, r=8, t=20, b=8))
                    st.plotly_chart(line, width="stretch", theme=None)
                else:
                    st.info("No trend data for this selection.")
            with right:
                st.markdown("##### Rating score distribution")
                distribution = cached_distribution(active, genre, start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
                dist = px.bar(distribution, x="rating", y="rating_count", labels={"rating": "Score", "rating_count": "Reviews"},
                              color_discrete_sequence=[PRIMARY_BLUE])
                dist.update_layout(height=380, margin=dict(l=8, r=8, t=20, b=8))
                dist.update_xaxes(dtick=0.5, gridcolor="#E9EEF5")
                st.plotly_chart(dist, width="stretch", theme=None)
            source_comparison_chart([genre], "detail")

with compare_tab:
    if compare_tab.open:
        st.subheader("Genre vs. genre")
        compared = st.multiselect("Choose genres", available_genres, key="compare_genres",
                                  default=remembered("compare_genres", available_genres, available_genres[:2]))
        compared_summary = summary[summary["genre"].isin(compared)].copy() if compared else pd.DataFrame()
        if len(compared) < 2:
            st.info("Choose at least two genres to create a comparison.")
        elif compared_summary.empty:
            st.info("No selected genres match the active filters.")
        else:
            compare_left, compare_right = st.columns(2)
            with compare_left:
                avg_chart = px.bar(compared_summary.sort_values("average_rating", ascending=False),
                                   x="genre", y="average_rating", color="genre", color_discrete_map=genre_color_map, text_auto=".2f",
                                   labels={"genre": "Genre", "average_rating": "Average rating"})
                avg_chart.update_layout(height=380, showlegend=False, yaxis_range=[0, 5],
                                        margin=dict(l=8, r=8, t=25, b=8))
                st.plotly_chart(avg_chart, width="stretch", theme=None)
            with compare_right:
                reach_measures = ["rating_count", "reviewer_count"]
                reach_names = {"rating_count": "Reviews", "reviewer_count": "Distinct reviewers"}
                reach = compared_summary.melt(id_vars="genre", value_vars=reach_measures, var_name="measure", value_name="count")
                reach["measure"] = reach["measure"].map(reach_names)
                reach_chart = px.bar(reach, x="genre", y="count", color="measure", barmode="group",
                                     color_discrete_sequence=[PRIMARY_BLUE, "#F28E2B"],
                                     labels={"genre": "Genre", "count": "Count", "measure": "Measure"})
                reach_chart.update_layout(height=380, margin=dict(l=8, r=8, t=25, b=8), legend_title=None)
                st.plotly_chart(reach_chart, width="stretch", theme=None)

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
                st.plotly_chart(year_chart, width="stretch", theme=None)
            source_comparison_chart(compared, "compare")
            compare_table = compared_summary.rename(columns={"genre": "Genre", "average_rating": "Avg rating",
                                                             "rating_count": "Reviews", "reviewer_count": "Reviewers",
                                                             "release_year_count": "Release years"})
            compare_table["Avg rating"] = compare_table["Avg rating"].round(2)
            compare_columns = ["Genre", "Avg rating", "Reviews", "Reviewers", "Release years"]
            st.dataframe(compare_table[compare_columns], width="stretch", hide_index=True)

with period_compare_tab:
    if period_compare_tab.open:
        st.subheader("Compare time periods")

        compare_filters = st.columns([1.35, 1.25, 1.15, 1.1])
        with compare_filters[0]:
            compare_dimension = st.selectbox("Compare by", ["Rating year", "Film release year", "Month", "Season", "Day of week", "Day of month", "Special date"],
                                             key="period_dimension")
        with compare_filters[1]:
            compare_genre_label = st.selectbox("Genre", ["All genres (no genre filter)", *available_genres], key="period_genre",
                                                help="Choose all genres together, or focus on one genre.")
            compare_genre = None if compare_genre_label == "All genres (no genre filter)" else compare_genre_label
        with compare_filters[2]:
            compare_measure_label = st.selectbox("Measure", ["Average score", "Number of reviews", "Distinct reviewers"],
                                                 key="period_measure")
            compare_measure = {"Average score": "average_rating", "Number of reviews": "rating_count", "Distinct reviewers": "reviewer_count"}[compare_measure_label]
        with compare_filters[3]:
            if compare_dimension == "Special date":
                compare_window = st.slider("Days before/after event", min_value=0, max_value=14, key="period_window",
                                           value=None if "period_window" in st.session_state else 3)
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
                group_measure_label = compare_measure_label
        else:
            dimension_key = {"Rating year": "Rating year", "Film release year": "Release year",
                             "Month": "Month", "Season": "Season", "Day of week": "Weekday",
                             "Day of month": "Day of month"}[compare_dimension]
            period_data = cached_time_comparison(active, dimension_key, compare_genre,
                                                 start_date.isoformat(), end_date.isoformat(), release_filter, season_filter, minimum_score)
            period_data = period_data[period_data["rating_count"] >= minimum_ratings].copy()
            group_measure_label = compare_measure_label
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
            selected_groups = st.multiselect("Compare groups", group_options, placeholder="Every period",
                                             default=remembered(f"period_groups_{compare_dimension}", group_options, None),
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
                    chart_measure_label = "Avg. reviews per occurrence of this date number"
                if compare_dimension == "Special date":
                    if compare_measure == "rating_count":
                        chart_measure_label = "Avg. reviews per event window"
                    elif compare_measure == "reviewer_count":
                        chart_measure_label = "Avg. distinct reviewers per event window"
                metric_values = period_data[active_measure]
                if compare_measure == "average_rating":
                    peak_value = period_data.loc[metric_values.idxmax()]
                    st.metric("Highest average score", f"{peak_value['time_group']} · {peak_value['average_rating']:.2f} / 5")
                elif compare_measure == "rating_count":
                    peak_value = period_data.loc[metric_values.idxmax()]
                    if compare_dimension == "Day of month":
                        st.metric("Highest average date activity", f"Day {peak_value['time_group']} · {peak_value['average_per_occurrence']:,.1f} / occurrence")
                    elif compare_dimension == "Special date":
                        st.metric("Most review activity", f"{peak_value['time_group']} · {peak_value['rating_count']:,.0f} / window")
                    else:
                        st.metric("Most review activity", f"{peak_value['time_group']} · {peak_value['rating_count']:,.0f}")
                else:
                    peak_value = period_data.loc[metric_values.idxmax()]
                    st.metric("Most distinct reviewers", f"{peak_value['time_group']} · {peak_value['reviewer_count']:,.0f}")

                chart_data = period_data.copy()
                if compare_dimension in {"Rating year", "Film release year"}:
                    chart_data["sort_key"] = pd.to_numeric(chart_data["time_group"], errors="coerce")
                    chart_data = chart_data.sort_values("sort_key")
                    period_chart = px.line(chart_data, x="time_group", y=active_measure, markers=True,
                                           labels={"time_group": compare_dimension, active_measure: chart_measure_label,
                                                   "rating_count": "Reviews", "reviewer_count": "Reviewers"},
                                           hover_data={"rating_count": ":,", "reviewer_count": ":,"})
                else:
                    chart_data = chart_data.sort_values(active_measure, ascending=True)
                    period_chart = px.bar(chart_data, x=active_measure, y="time_group", orientation="h",
                                          color=active_measure, color_continuous_scale=[[0, "#DDF1FF"], [1, PRIMARY_BLUE]],
                                          labels={"time_group": compare_dimension, active_measure: chart_measure_label,
                                                  "rating_count": "Reviews", "reviewer_count": "Reviewers"},
                                          hover_data={"rating_count": ":,", "reviewer_count": ":,"})
                period_chart.update_layout(height=max(360, min(650, 36 * len(chart_data) + 100)),
                                           coloraxis_showscale=False, margin=dict(l=8, r=8, t=20, b=8))
                if compare_measure == "average_rating":
                    if compare_dimension in {"Rating year", "Film release year"}:
                        period_chart.update_yaxes(range=[0, 5])
                    else:
                        period_chart.update_xaxes(range=[0, 5], dtick=0.5)
                st.plotly_chart(period_chart, width="stretch", theme=None)

                table_columns = ["time_group", "average_rating", "rating_count", "reviewer_count"]
                if "periods" in period_data.columns:
                    table_columns.append("periods")
                if compare_dimension == "Day of month":
                    table_columns.extend(["average_per_occurrence", "date_occurrences"])
                table_rating_label = "Avg. reviews per event window" if compare_dimension == "Special date" else "Reviews"
                table = period_data[table_columns].copy().rename(columns={"time_group": "Compared period",
                    "average_rating": "Avg. score", "rating_count": table_rating_label,
                    "reviewer_count": "Reviewers", "periods": "Years with data",
                    "average_per_occurrence": "Avg. reviews per occurrence", "date_occurrences": "Months represented"})
                if "Avg. score" in table.columns:
                    table["Avg. score"] = table["Avg. score"].round(2)
                st.dataframe(table, width="stretch", hide_index=True)

with timing_tab:
    if timing_tab.open:
        st.subheader("Month-by-month audience activity")

        with st.expander("Chart options", expanded=False):
            timing_filters = st.columns([1, 1])
            with timing_filters[0]:
                timing_genre_label = st.selectbox("Genre", ["All genres (no genre filter)", *available_genres], key="timing_genre",
                                                   help="Choose all genres together, or focus on one genre.")
                timing_genre = None if timing_genre_label == "All genres (no genre filter)" else timing_genre_label
            with timing_filters[1]:
                metric_label = st.selectbox("Measure", ["Number of reviews", "Distinct reviewers", "Average score"],
                                            key="timing_metric")
                metric_column = {"Number of reviews": "rating_count", "Distinct reviewers": "reviewer_count", "Average score": "average_rating"}[metric_label]
                metric_axis = {"Number of reviews": "Reviews submitted", "Distinct reviewers": "Distinct reviewers", "Average score": "Average score (out of 5)"}[metric_label]
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
            month_metrics[0].metric("Most review activity", f"{best_month['month_name']} · {best_month['rating_count']:,}")
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
                                    labels={"x": "Month", "y": "Rating year", "color": metric_axis})
                heatmap.update_traces(hovertemplate="Year: %{y}<br>Month: %{x}<br>" + metric_axis + ": %{z:" + heatmap_format + "}<extra></extra>")
                heatmap.update_layout(height=max(330, min(620, 22 * len(month_grid) + 100)),
                                      margin=dict(l=8, r=8, t=18, b=8))
                if metric_column == "average_rating":
                    heatmap.update_coloraxes(cmin=0, cmax=5)
                st.plotly_chart(heatmap, width="stretch", theme=None)
            else:
                st.info("No year-month cells meet this minimum activity threshold.")

            st.markdown("##### Which months have the most activity?")
            month_chart = px.bar(monthly.sort_values("month"), x="month_name", y=metric_column,
                                 hover_data={"rating_count": ":,", "reviewer_count": ":,", "average_rating": ":.2f"},
                                 labels={"month_name": "Rating month", metric_column: metric_axis,
                                         "rating_count": "Reviews", "reviewer_count": "Reviewers",
                                         "average_rating": "Average score"}, color_discrete_sequence=[PRIMARY_BLUE])
            month_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
            if metric_column == "average_rating":
                month_chart.update_yaxes(range=[0, 5])
            st.plotly_chart(month_chart, width="stretch", theme=None)

            st.markdown("##### When people submit the most reviews")
            timing_left, timing_right = st.columns(2)
            if not weekdays.empty:
                peak_weekday = weekdays.loc[weekdays["rating_count"].idxmax()]
                timing_left.metric("Most active review weekday", f"{peak_weekday['weekday_name']} · {peak_weekday['rating_count']:,}")
                weekday_chart = px.bar(weekdays, x="weekday_name", y=metric_column,
                                       category_orders={"weekday_name": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]},
                                       labels={"weekday_name": "Rating submitted on", metric_column: metric_axis},
                                       color_discrete_sequence=[PRIMARY_BLUE])
                weekday_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
                if metric_column == "average_rating":
                    weekday_chart.update_yaxes(range=[0, 5])
                timing_left.plotly_chart(weekday_chart, width="stretch", theme=None)
            if not day_numbers.empty:
                day_metric = "average_per_occurrence" if metric_column == "rating_count" else metric_column
                day_axis = "Avg. reviews per occurrence of that date" if metric_column == "rating_count" else metric_axis
                peak_day = day_numbers.loc[day_numbers[day_metric].idxmax()]
                peak_value = peak_day["average_per_occurrence"] if metric_column == "rating_count" else peak_day[metric_column]
                timing_right.metric("Most active calendar date", f"Day {int(peak_day['day_of_month'])} · {peak_value:,.1f}")
                day_chart = px.bar(day_numbers, x="day_of_month", y=day_metric,
                                   hover_data={"occurrence_count": True, "rating_count": ":,", "average_rating": ":.2f"},
                                   labels={"day_of_month": "Day of month when rating was submitted", day_metric: day_axis,
                                            "occurrence_count": "Months represented", "rating_count": "Reviews",
                                           "average_rating": "Average score"},
                                   color_discrete_sequence=[PRIMARY_BLUE])
                day_chart.update_layout(height=330, margin=dict(l=8, r=8, t=18, b=8))
                day_chart.update_xaxes(dtick=1)
                if metric_column == "average_rating":
                    day_chart.update_yaxes(range=[0, 5])
                timing_right.plotly_chart(day_chart, width="stretch", theme=None)
with special_tab:
    if special_tab.open:
        st.subheader("Special dates & holiday periods")

        special_order = SPECIAL_DATE_ORDER
        with st.expander("Special date filters", expanded=True):
            date_options = st.columns([1.4, 1])
            with date_options[0]:
                chosen_events = st.multiselect("Dates to compare", special_order,
                                               placeholder="Every special date", key="special_event_compare",
                                               help="Leave empty to include all dates in the list.")
            with date_options[1]:
                window_days = st.slider("Event window (days before and after)", 0, 14, key="special_window",
                                        value=None if "special_window" in st.session_state else 3)

        with st.expander("Chart options", expanded=False):
            special_options = st.columns([1.4, 1])
            with special_options[0]:
                default_genres = available_genres[:2]
                special_genres = st.multiselect("Genres to compare", available_genres,
                                                default=remembered("special_genres", available_genres, default_genres),
                                                placeholder="Select one or more genres", key="special_genres",
                                                help="Each selected genre appears as its own series in the chart.")
            with special_options[1]:
                special_measure_label = st.selectbox("Chart measure", ["Average score", "Number of reviews", "Distinct reviewers"],
                                                     
                                                     key="special_measure")

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
            measure_axis = measure_axis
            sort_order = event_summary.groupby("event")[measure_column].mean().sort_values().index
            special_chart = px.bar(event_summary, x=measure_column, y="event", color="genre", color_discrete_map=genre_color_map, barmode="group",
                                   orientation="h", category_orders={"event": list(sort_order)},
                                   hover_data=({"average_rating": ":.2f"}) | {"rating_count": ":.1f", "reviewer_count": ":.1f", "event_years": True},
                                   labels={"event": "Special date", "genre": "Genre", measure_column: measure_axis,
                                            "average_rating": "Avg. score", "rating_count": "Avg. reviews/window",
                                           "reviewer_count": "Avg. reviewers/window", "event_years": "Years with data"})
            special_chart.update_layout(height=max(380, 42 * len(event_options)), margin=dict(l=8, r=8, t=24, b=8),
                                        legend_title_text="Genre")
            if measure_column == "average_rating":
                special_chart.update_xaxes(range=[0, 5], dtick=0.5)
            st.plotly_chart(special_chart, width="stretch", theme=None)

            event_table = event_summary[["event", "genre", "average_rating", "rating_count", "reviewer_count", "event_years"]].copy()
            event_table.columns = ["Special date", "Genre", "Avg. score", "Avg. reviews per window", "Avg. reviewers per window", "Years with data"]
            for column in ["Avg. score", "Avg. reviews per window", "Avg. reviewers per window"]:
                event_table[column] = event_table[column].round(2 if column == "Avg. score" else 1)
            st.dataframe(event_table, width="stretch", hide_index=True)

            st.markdown("##### How one special date changes over time")
            history_columns = st.columns([1.2, 1])
            with history_columns[0]:
                history_event = st.selectbox("Track a date", event_options, key="special_history_event")
            with history_columns[1]:
                history_measure = st.selectbox("Yearly measure", ["Average score", "Number of reviews", "Distinct reviewers"],
                                               key="special_history_measure")
            history_column = {"Number of reviews": "rating_count", "Average score": "average_rating",
                              "Distinct reviewers": "reviewer_count"}[history_measure]
            event_history = event_rows[event_rows["event"] == history_event].sort_values("event_year")
            history_chart = px.line(event_history, x="event_year", y=history_column, color="genre", color_discrete_map=genre_color_map, markers=True,
                                    hover_data=({"rating_count": ":,", "average_rating": ":.2f", "reviewer_count": ":,"}),
                                    labels={"event_year": "Year", history_column: history_measure,
                                            "rating_count": "Reviews", "average_rating": "Average score",
                                            "reviewer_count": "Distinct reviewers", "genre": "Genre"})
            history_chart.update_layout(height=360, margin=dict(l=8, r=8, t=20, b=8), legend_title_text="Genre")
            if history_column == "average_rating":
                history_chart.update_yaxes(range=[0, 5])
            st.plotly_chart(history_chart, width="stretch", theme=None)
