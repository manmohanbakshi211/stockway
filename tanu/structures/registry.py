"""Multi-leg option structures (bullets): iron condors, short straddles / strangles and the like, entered by the
calendar (sessions before expiry, a time of day) and held to expiry or to a time, with stops on the whole structure.

A structure file (tanu/structures/library/*.json) says: which symbols; when to enter (`dte` sessions before the
expiry, 0 = on expiry day; `at` HH:MM); its legs (sell / buy, CE / PE, `pct` = strike distance from spot in %:
+1.0 = 1% above, -1.0 = 1% below, 0 = at the money); when to leave (`at`: "expiry" = settle at the index close on
expiry day, or "HH:MM" on the entry day; `stop_x_credit`: buy everything back when the loss reaches this many times
the credit; `take_pct_credit`: when this % of the credit is kept). `dte` null = enter every session (then `exit.at`
must be a time). Loaded in isolation: a bad file is skipped and logged. New structures start in shadow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import log

LIB = Path(__file__).with_name("library")
_log = log.get("structures")


class Leg(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["sell", "buy"]
    side: Literal["CE", "PE"]
    pct: float = Field(ge=-15, le=15)


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dte: int | None = Field(None, ge=0, le=30)      # sessions before expiry; None = every session
    at: str = "15:15"


class Leave(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    at: str = "expiry"
    stop_x_credit: float | None = Field(None, gt=0)
    take_pct_credit: float | None = Field(None, gt=0, lt=100)


class Structure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    why: str
    source: str
    valid_from: str
    status: Literal["shadow", "trial", "live", "retired"] = "shadow"
    symbols: list[str]
    entry: Entry
    legs: list[Leg] = Field(min_length=1, max_length=6)
    exit: Leave = Leave()
    lots: int = Field(1, ge=1, le=10)
    max_strike_gap_pct: float = Field(0.3, gt=0)   # skip if no listed strike is this close to the wanted one

    @field_validator("exit")
    @classmethod
    def _exit(cls, v, info):
        e = info.data.get("entry")
        if e is not None and e.dte is None and v.at == "expiry":
            raise ValueError("an every-session structure needs exit.at as a time, not expiry")
        return v

    @field_validator("id")
    @classmethod
    def _id(cls, v):
        if not v.replace("_", "").isalnum():
            raise ValueError("id: letters, digits, underscores")
        return v

    @property
    def defined_risk(self) -> bool:
        """Every sold leg has a bought leg of the same side further out: the loss is capped."""
        for lg in self.legs:
            if lg.action != "sell":
                continue
            wing = [b for b in self.legs if b.action == "buy" and b.side == lg.side and
                    (b.pct > lg.pct if lg.side == "CE" else b.pct < lg.pct)]
            if not wing:
                return False
        return True


def load(folder: Path | str = LIB) -> tuple[list[Structure], list[dict]]:
    good, bad = [], []
    for f in sorted(Path(folder).glob("*.json")):
        try:
            good.append(Structure(**json.loads(f.read_text())))
        except Exception as e:                                       # a broken bullet never stops the gun
            bad.append({"file": f.name, "error": str(e)[:300]})
            _log.warning("bullet_failed", file=f.name, error=str(e)[:300])
    return good, bad
