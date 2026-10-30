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


def _stabilise_process():
    """Keep the long-running app from running out of open files (macOS allows only 256 by default).

    1. yfinance keeps small SQLite caches (time zones, cookies, ISINs) and opens a new connection to them in every
       thread that downloads, never closing it. The app downloads from new threads all day, so on a Mac the process
       hit 'Too many open files' within minutes. The caches only save a lookup, so they are switched off.
    2. The open-files limit is raised as far as the system allows, as a safety margin.
    """
    try:
        import yfinance.cache as yc
        for mgr, exc in (("_TzDBManager", "_TzCacheException"), ("_CookieDBManager", "_CookieCacheException"),
                         ("_ISINDBManager", "_ISINCacheException")):
            m, e = getattr(yc, mgr, None), getattr(yc, exc, None)
            if m is not None and e is not None:
                def _off(cls, _e=e):
                    raise _e("Stockway: yfinance cache off (it leaked one open file per thread)")
                m.get_database = classmethod(_off)
    except Exception:
        pass
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 10240 if hard == resource.RLIM_INFINITY else min(hard, 10240)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except Exception:
        pass


_stabilise_process()
