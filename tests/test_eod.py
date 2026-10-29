"""End-of-day (bhavcopy) strategy maths."""
import datetime as dt
import io
import zipfile

import pandas as pd

from lab import eod_study as E
from lab.bhav import parse


def test_parse_old_and_new_formats():
    old = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n" \
          "OPTIDX,NIFTY,30-Jan-2020,12000,CE,100,110,90,105,105,500,1,1000,10,01-JAN-2020,\n" \
          "OPTSTK,RELIANCE,30-Jan-2020,1500,CE,10,11,9,10,10,5,1,10,1,01-JAN-2020,\n"
    new = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,"
           "OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
           "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
           "2025-10-27,2025-10-27,FO,NSE,IDO,1,,NIFTY,,2025-10-28,2025-10-28,25900,PE,x,50,60,40,55,55,52,25960,55,1000,10,999,1,1,F1,75,,,,,\n")
    def z(txt):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as f:
            f.writestr("x.csv", txt)
        return b.getvalue()
    a = parse(z(old), dt.date(2020, 1, 1))
    b = parse(z(new), dt.date(2025, 10, 27))
    assert len(a) == 1 and a.iloc[0]["sym"] == "NIFTY" and a.iloc[0]["expiry"] == dt.date(2020, 1, 30)
    assert len(b) == 1 and b.iloc[0]["side"] == "PE" and b.iloc[0]["underlying"] == 25960


def test_iron_condor_settles_at_intrinsic():
    book = {(25200.0, "CE"): 20.0, (24800.0, "PE"): 20.0, (25400.0, "CE"): 5.0, (24600.0, "PE"): 5.0}
    legs = E.legs_for("iron_condor", {"short": 0.8, "wing": 0.8}, 25000, 50)
    assert legs == [(25200, "CE", -1), (24800, "PE", -1), (25400, "CE", 1), (24600, "PE", 1)]
    calm = E.simulate(legs, book, 25010, 65)            # expires between the shorts: keep the credit minus costs
    crash = E.simulate(legs, book, 24000, 65)           # far below: max loss = wing - credit
    assert calm > 1500 and -14000 < crash < -10000
