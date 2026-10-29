"""Insider disclosures and NSE results parsing."""
from live.insiders import meaningful, normalise, parse_xbrl


def test_normalise_and_filter():
    r = {"acqMode": "Market Purchase", "personCategory": "Promoter Group", "tdpTransactionType": "Buy", "secVal": "215800000",
         "acqName": "X Ltd", "symbol": "BAJFINANCE", "pid": "1", "date": "30-Mar-2026 20:26", "acqtoDt": "25-Mar-2026",
         "befAcqSharesPer": "1.2", "afterAcqSharesPer": "1.3"}
    x = normalise(r)
    assert x["side"] == "Buy" and x["market"] and x["key"] and x["days_to_file"] == 5 and meaningful(x)
    off = normalise({**r, "acqMode": "Off Market"})
    assert not off["market"] and not meaningful(off)
    small = normalise({**r, "secVal": "100000"})
    assert not meaningful(small)


def test_parse_xbrl_current_quarter():
    xml = ('<in-capmkt:RevenueFromOperations contextRef="OneD" unitRef="INR">492440000000</in-capmkt:RevenueFromOperations>'
           '<in-capmkt:RevenueFromOperations contextRef="OneI" unitRef="INR">900000000000</in-capmkt:RevenueFromOperations>'
           '<in-capmkt:ProfitLossForPeriod contextRef="OneD" unitRef="INR">78380000000</in-capmkt:ProfitLossForPeriod>'
           '<in-capmkt:DilutedEarningsPerShareFromContinuingOperations contextRef="OneD" unitRef="INRPerShare">19.3</in-capmkt:DilutedEarningsPerShareFromContinuingOperations>')
    f = parse_xbrl(xml)
    assert f["RevenueFromOperations"] == 492440000000 and f["ProfitLossForPeriod"] == 78380000000 and f["eps"] == 19.3
