"""Bank Nifty Opportunity Radar."""
import warnings

# yfinance uses pandas features that newer pandas flags as deprecated; harmless, hide the noise
warnings.filterwarnings("ignore", module=r"yfinance")
warnings.filterwarnings("ignore", message=r".*inplace keyword.*")

# Newer Python (3.14) can keep warning filters per thread, so the filters above may not reach the threads that
# download prices. Drop these library notices at the point they are printed instead.
_show = warnings.showwarning


def _quiet_showwarning(message, category, filename, lineno, file=None, line=None):
    text = str(message)
    if "yfinance" in str(filename) or "inplace keyword" in text or "PDEP-8" in text:
        return
    _show(message, category, filename, lineno, file, line)


warnings.showwarning = _quiet_showwarning
