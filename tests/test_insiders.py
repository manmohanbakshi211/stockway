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


def test_parse_new_pit_xbrl():
    from live.insiders import parse_pit_xml
    xml = ('<in-bse-co:Symbol contextRef="MainI">ABC</in-bse-co:Symbol>'
           '<in-bse-co:CategoryOfPerson contextRef="Disclosure1">Promoter Group</in-bse-co:CategoryOfPerson>'
           '<in-bse-co:NameOfThePerson contextRef="Disclosure1">X Holdings</in-bse-co:NameOfThePerson>'
           '<in-bse-co:SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding contextRef="Disclosure1" unitRef="pure">0.0120</in-bse-co:SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding>'
           '<in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity contextRef="Disclosure1" unitRef="INR">150000000</in-bse-co:SecuritiesAcquiredOrDisposedValueOfSecurity>'
           '<in-bse-co:SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure1">Buy</in-bse-co:SecuritiesAcquiredOrDisposedTransactionType>'
           '<in-bse-co:SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding contextRef="Disclosure1" unitRef="pure">0.0135</in-bse-co:SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding>'
           '<in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate contextRef="Disclosure1">2026-10-23</in-bse-co:DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate>'
           '<in-bse-co:ModeOfAcquisitionOrDisposal contextRef="Disclosure1">Market Purchase</in-bse-co:ModeOfAcquisitionOrDisposal>'
           '<in-bse-co:CategoryOfPerson contextRef="Disclosure2">Designated Person</in-bse-co:CategoryOfPerson>'
           '<in-bse-co:SecuritiesAcquiredOrDisposedTransactionType contextRef="Disclosure2">Pledge</in-bse-co:SecuritiesAcquiredOrDisposedTransactionType>'
           '<in-bse-co:ModeOfAcquisitionOrDisposal contextRef="Disclosure2">Pledge Creation</in-bse-co:ModeOfAcquisitionOrDisposal>')
    rows = parse_pit_xml(xml, {"appId": "7", "symbol": "ABC", "broadcastDateTime": "27-Oct-2026 20:28:28"})
    a = [r for r in rows if r["pid"] == "g7:Disclosure1"][0]
    assert a["side"] == "Buy" and a["market"] and a["key"] and meaningful(a)
    assert abs(a["before_pct"] - 1.2) < 1e-9 and a["days_to_file"] == 4 and a["filed"].startswith("2026-10-27")
    b = [r for r in rows if r["pid"] == "g7:Disclosure2"][0]
    assert not meaningful(b)
