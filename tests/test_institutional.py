"""Tests for institutional fund universe + overhead ticker strip (no network)."""

from __future__ import annotations

from stock_alert_app import institutional
from stock_alert_app.db import Database
from stock_alert_app.universe import universe


def _filer_row(cik: str, name: str) -> str:
    return (
        "<tr>"
        f'<td><a href="/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR">'
        f"{cik}</a></td>"
        f'<td><a href="/Archives/edgar/data/{cik}/1-index.htm">{name}</a></td>'
        "<td>2024-08-14</td><td>13F-HR</td>"
        "</tr>"
    )


def _filer_page(rows: str, start: int, count: int, total: int) -> str:
    hi = min(start + count, total)
    return (
        f"<table>{rows}</table>"
        f"<div>Viewing {start + 1} - {hi} of {total}</div>"
    )


def test_parse_filer_table_extracts_cik_and_name():
    html = _filer_row("0001350694", "BRIDGEWATER ASSOCIATES, LP") + _filer_row(
        "0001037389", "RENAISSANCE TECHNOLOGIES LLC"
    )
    rows = institutional._parse_filer_table(html)
    assert rows == [
        ("1350694", "BRIDGEWATER ASSOCIATES, LP"),
        ("1037389", "RENAISSANCE TECHNOLOGIES LLC"),
    ]


def test_parse_filer_table_returns_empty_for_garbage():
    assert institutional._parse_filer_table("") == []
    assert institutional._parse_filer_table("<html>no table here</html>") == []


def test_sec_13f_filers_paginates_and_dedupes(monkeypatch):
    # Two pages, 100 filers per page; a duplicate CIK appears on page 2.
    page1_ciks = [f"1{i:08d}" for i in range(100)]  # 100000000 .. 100000099
    rows_page1 = "".join(_filer_row(cik, f"FUND {cik}") for cik in page1_ciks)
    dup = page1_ciks[0]
    new = "200000000"
    rows_page2 = _filer_row(dup, "FUND DUP") + _filer_row(new, "FUND NEW")
    pages = {
        0: _filer_page(rows_page1, 0, 100, 101),
        100: _filer_page(rows_page2, 100, 100, 101),
    }

    def fake_http_get(url: str) -> str:
        start = int(url.split("start=")[1].split("&")[0])
        return pages[start]

    monkeypatch.setattr(institutional, "_http_get", fake_http_get)

    filers = institutional.sec_13f_filers(page_size=100, max_pages=10)
    # 100 unique from page 1 + 1 new from page 2 (dup dropped).
    assert len(filers) == 101
    ciks = [f["cik"] for f in filers]
    assert len(ciks) == len(set(ciks))
    assert new in ciks


def test_sec_13f_filers_respects_max_pages(monkeypatch):
    # A runaway source should not loop forever: max_pages caps the loop.
    def fake_http_get(url: str) -> str:
        return _filer_page(_filer_row("0001350694", "BRIDGEWATER ASSOCIATES, LP"), 0, 100, 99999)

    monkeypatch.setattr(institutional, "_http_get", fake_http_get)
    filers = institutional.sec_13f_filers(max_pages=3, page_size=100)
    assert len(filers) == 1  # deduped across the repeated pages


def test_ticker_strip_snapshots_computes_change(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema()
    universe(db)  # seed securities so display metadata joins in

    db.insert_price_snapshot("LSE", "ULVR", close=100.0, open=99.0, high=101.0, low=98.0, volume=1000)
    db.insert_price_snapshot("LSE", "ULVR", close=110.0, open=108.0, high=111.0, low=107.0, volume=1200)
    db.insert_price_snapshot("NYSE", "AAPL", close=200.0, open=199.0, high=201.0, low=198.0, volume=500)

    rows = db.ticker_strip_snapshots()
    by_id = {(r["market"], r["ticker"]): r for r in rows}

    ulvr = by_id[("LSE", "ULVR")]
    assert ulvr["close"] == 110.0
    assert abs(ulvr["change_pct"] - 0.10) < 1e-9

    aapl = by_id[("NYSE", "AAPL")]
    assert aapl["close"] == 200.0
    assert aapl["change_pct"] is None  # only one snapshot -> NO_DATA, not invented

    assert set(by_id) == {("LSE", "ULVR"), ("NYSE", "AAPL")}
