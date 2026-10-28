"""Bank Nifty Opportunity Radar."""
import warnings

# yfinance uses pandas features that newer pandas flags as deprecated; harmless, hide the noise
warnings.filterwarnings("ignore", module=r"yfinance")
warnings.filterwarnings("ignore", message=r".*inplace keyword.*")
