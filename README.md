# Movie ratings project — run and folder guide

This handoff has exactly three main folders:

1. **01_Movie_Rating_System** — the Streamlit app and its two quick-load datasets: MovieLens small and the 500k-rating sample.
2. **02_Databases** — year-separated MovieLens CSV datasets, the 1m-rating analysis sample, and the SQLite snapshot cache created by the app.
3. **03_Assignment_Requirements** — the notebook, data processor, required CSV files, three visualizations, and report.

The two largest full catalog imports are left out of this clean handoff. The year-separated data is stored in its own subfolder. Only movies.csv, ratings.csv, and README.txt are kept for each dataset to reduce extra files.

## Run the dashboard in VS Code

1. Open **01_Movie_Rating_System** as the VS Code folder.
2. Open Terminal → New Terminal and run:

   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python -m streamlit run main.py

3. In the app sidebar, choose **Add a dataset**, select either **ml-latest-small** or **ml-32m-2000-2023-500k**, and import it. The import creates a database snapshot under **02_Databases\cache**.

If PowerShell blocks activation, run the install and Streamlit commands in the same VS Code terminal using the selected Python interpreter.

## Rebuild the assignment outputs

Use the same Python environment after installing the requirements. Open a terminal in **03_Assignment_Requirements** and run:

   python data_processor.py

The processor reads the 1m-rating sample from **02_Databases\samples** and recreates the CSV summaries, charts, and Markdown report in the assignment folder. The PDF is the supplied report export.

## What the app does

The app imports MovieLens CSV files into SQLite so charts and tables can query the data without loading every rating into memory. The small dataset is fastest for a quick start; the 500k sample gives a larger example. Rating counts show submitted ratings, not verified movie views.


The Streamlit first-run email prompt is disabled for this project in .streamlit\config.toml.

## Web scraping: box office #1 films

The sidebar has a **Scrape box office data** panel. Pick countries and years and press **Scrape latest data**. The app then:

1. Downloads each "List of YEAR box office number-one films in COUNTRY" page from Wikipedia through the MediaWiki API (one request per country and year, with a pause between requests and a descriptive User-Agent).
2. Parses the weekend tables with BeautifulSoup, including merged cells for films that stayed #1 for several weeks, and saves the weekend date, film, Wikipedia link and gross.
3. Finds genres for each film: MovieLens genres when the film is in the working dataset (title + year match), otherwise the film's Wikidata genres mapped onto the same MovieLens genre names.

Results are stored in **02_Databases\web_scrape\box_office_weekends.sqlite3**, separate from the MovieLens snapshots. Saved years are skipped on later runs; the two most recent years are always refreshed. Countries or years without a Wikipedia page are logged as missing and do not stop the run.

### Using the scraped data in the dashboard

Above **Filters for every view** there is a **Data source** switch:

- **Reviews (MovieLens)**: the original ratings.
- **Box office #1 (scraped)**: every tab (Overview, Genre detail, Compare genres, Compare time periods, Month patterns, Special dates) runs on the scraped weekends instead. Pick countries next to the switch, or leave it empty for all scraped countries.

The app turns the scraped weekends into a database with the same tables as a MovieLens snapshot (one row per country per #1 weekend, the country in place of the reviewer, the weekend end date in place of the rating date), so the same queries and filters work. Labels change to "#1 weekends" and "Countries". Box office data has no score, so score charts switch to counts and shares, and the Minimum score filter is disabled.

**Genre detail** and **Compare genres** also show **Reviews vs. box office over time**: each genre's share of all reviews (solid line) and of all #1 weekends (dashed line) per year, so you can see how a genre's popularity changed in both sources, including years after the MovieLens data ends.

Install the two new packages with `python -m pip install -r requirements.txt` (requests and beautifulsoup4 were added).

## Speed

The dashboard reads the working snapshot into memory once (`fast_queries.py`) and answers every filter from there, so changes take well under a second even on the 500k dataset (the first open takes a few seconds while the data loads). Only the selected tab is calculated, and a "Loading data…" bar appears at the top whenever an update takes longer than a moment. `fast_queries.py` returns exactly the same results as the SQL versions in `data_store.py`.
