"""Command-line training (the app's "Train model" button does the same).

    python run_all.py              # real data (needs internet)
    python run_all.py --no-news    # skip GDELT news
    python run_all.py --demo       # synthetic data, offline test
"""
import argparse

from radar import pipeline
from radar.config import load_config

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--no-news", action="store_true")
    a = ap.parse_args()
    pipeline.train(load_config(), demo=a.demo, use_news=not a.no_news)
