import sqlite3
import pandas as pd

conn = sqlite3.connect("data/tse_eventbase.db")
events = pd.read_sql_query("""
    SELECT ticker, event_date, event_time, event_type, headline
    FROM events
    WHERE event_type IN (
        'earnings', 'forecast_revision', 'dividend',
        'buyback', 'ma', 'tender_offer'
    )
    ORDER BY event_date, ticker
""", conn)
conn.close()

events.to_csv("data/exports/event_filter_list.csv", index=False)
print(f"Filter list: {len(events)} events across {events['ticker'].nunique()} tickers")
print(f"Date range: {events['event_date'].min()} to {events['event_date'].max()}")

events['year'] = pd.to_datetime(events['event_date']).dt.year
print(events.groupby('year').agg(
    events=('ticker', 'count'),
    unique_dates=('event_date', 'nunique'),
    unique_tickers=('ticker', 'nunique')
))
