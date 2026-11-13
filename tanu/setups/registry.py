"""Setup bullets: one JSON file per setup in tanu/setups/library/. Each is validated on its own; a bad file is skipped
and reported, never stops the rest (ADR-0003).

A setup says, for each minute and each direction d (+1 = buy calls, -1 = buy puts):
  when   : all conditions true, each [feature, op, value]; features prefixed "d_" are multiplied by d first
           (so "d_move > 0" means "with the day's move"); "prev:" before a feature reads the previous minute
  window : entry minutes allowed, "HH:MM"-"HH:MM"
  exit   : chandelier trail on the underlying (stop atr_init x ATR, then best price - atr_trail x ATR), a premium stop,
           and flat by a time; or type "rr": stop at an underlying level taken from a feature at the signal
           (stop_feature), target at target_r x that risk
One trade per setup, symbol, day and direction (max_per_day allows re-entries, one position at a time).
Status: shadow | trial | live | retired.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LIBRARY = Path(__file__).with_name("library")
OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


class Exit(BaseModel):
    """type chandelier: stop on the underlying (atr_init x ATR, then best - atr_trail x ATR); premium / time: no
    underlying stop. Every type also has: a premium stop, an optional premium trail (after the option is up
    trail_after_pct, exit if it falls prem_trail_pct from its best), an optional time stop (max_minutes), an optional
    "no move" exit (after no_move_min minutes the option never got no_move_gain_pct up), and flat by a time."""
    model_config = ConfigDict(extra="forbid")
    name: str = "chandelier"
    type: Literal["chandelier", "premium", "time", "rr"] = "chandelier"
    atr_init: float = Field(1.5, gt=0)
    atr_trail: float = Field(2.5, gt=0)
    premium_stop_pct: float = Field(-40, lt=0)
    prem_trail_pct: float | None = Field(None, gt=0, lt=100)
    trail_after_pct: float = Field(10, ge=0)
    max_minutes: int | None = Field(None, gt=0)
    no_move_min: int | None = Field(None, gt=0)
    no_move_gain_pct: float = Field(5, ge=0)
    flat_by: str = "15:15"
    stop_feature: str | None = None              # type rr: feature holding the underlying stop level at the signal
    target_r: float | None = Field(None, gt=0)   # type rr: target = entry + direction x target_r x risk

    @model_validator(mode="after")
    def _rr(self):
        if self.type == "rr" and (not self.stop_feature or not self.target_r):
            raise ValueError("exit type rr needs stop_feature and target_r")
        return self


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    why: str
    source: str
    valid_from: str
    status: Literal["shadow", "trial", "live", "retired"] = "shadow"
    symbols: list[str]
    window: tuple[str, str]
    when: list[tuple[str, str, float | str]]
    exit: Exit = Exit()
    max_per_day: int = Field(1, ge=1, le=10)     # entries per symbol, day and direction (one open at a time)

    @field_validator("when")
    @classmethod
    def _ops(cls, v):
        for f, op, _ in v:
            if op not in OPS:
                raise ValueError(f"unknown operator {op!r} in condition on {f!r}")
        return v

    def window_minutes(self) -> tuple[int, int]:
        def m(hm):
            h, mm = hm.split(":")
            return (int(h) - 9) * 60 + int(mm) - 15
        return m(self.window[0]), m(self.window[1])


def load(folder: Path | str = LIBRARY) -> tuple[list[Setup], list[dict]]:
    good, bad = [], []
    for f in sorted(Path(folder).glob("*.json")):
        try:
            s = Setup(**json.loads(f.read_text()))
            if s.id != f.stem:
                raise ValueError(f"id {s.id!r} must match the file name {f.stem!r}")
            good.append(s)
        except Exception as e:
            bad.append({"file": f.name, "error": str(e)[:300]})
    return good, bad
