"""3.00: page answers turned into JSON in the worker thread that worked them out, not on the event loop (gun).

FastAPI runs a plain `def` endpoint in a worker thread, but then turns its answer into JSON (jsonable_encoder + dumps)
on the event loop: the one thread that also sends every page's live prices and quotes (/ws/prices, /ws/quotes). A big
answer (the Alerts page ~450 KB, the multi-timeframe view ~400 KB, the chart ~100 KB) held those streams while it was
encoded, so the Paper page's numbers stood still and other pages waited. Here the endpoint's dict / list is encoded
right where it was made. The bytes are the same as Starlette's JSONResponse (same json.dumps settings); anything plain
JSON can't take goes through FastAPI's own encoder, and an answer that can't be encoded this way (NaN) goes the usual
way, exactly as before.
"""
from __future__ import annotations

import functools
import inspect
import json

from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
from starlette.responses import Response


def render(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":"),
                      default=jsonable_encoder).encode("utf-8")


def _wrap(fn):
    @functools.wraps(fn)
    def w(*a, **k):
        r = fn(*a, **k)
        if isinstance(r, (dict, list)):
            try:
                return Response(render(r), media_type="application/json")
            except (ValueError, TypeError):
                return r                      # FastAPI's usual way (and its usual error), exactly as before
        return r
    w._fastjson = True
    return w


class FastJSONRoute(APIRoute):
    def __init__(self, path, endpoint, **kw):
        rm = kw.get("response_model")
        plain = rm is None or type(rm).__name__ == "DefaultPlaceholder"      # no response model set by hand
        if callable(endpoint) and not inspect.iscoroutinefunction(endpoint) and plain \
                and not getattr(endpoint, "_fastjson", False):
            endpoint = _wrap(endpoint)
        super().__init__(path, endpoint, **kw)
