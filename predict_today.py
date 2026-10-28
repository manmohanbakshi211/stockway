"""Command-line forecast for the next session (the app's "Update forecast" button does the same)."""
import argparse

from radar import pipeline
from radar.config import load_config

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-news", action="store_true")
    a = ap.parse_args()
    fc = pipeline.forecast(load_config(), demo=True if a.demo else None, use_news=not a.no_news)
    print("\n" + pipeline.forecast_text(fc))
