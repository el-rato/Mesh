from __future__ import annotations

import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .db import Database

logger = logging.getLogger(__name__)

EDGAR_USER_AGENT = "StockVerdictResearch contact@example.com"
INFO_TABLE_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"

# Big hedge funds / institutional asset managers with their SEC CIK.
# 13F filings are mandatory quarterly reports of US equity holdings.
MAJOR_FUNDS: list[dict[str, str]] = [
    {"name": "Bridgewater Associates", "cik": "0001350694"},
    {"name": "Renaissance Technologies", "cik": "0001037389"},
    {"name": "Citadel Advisors", "cik": "0001423053"},
    {"name": "Two Sigma Investments", "cik": "0001179393"},
    {"name": "Millennium Management", "cik": "0001273087"},
    {"name": "Point72 Asset Management", "cik": "0002006887"},
    {"name": "AQR Capital Management", "cik": "0001710537"},
    {"name": "D.E. Shaw Group", "cik": "0001009207"},
    {"name": "Marshall Wace", "cik": "0001718165"},
    {"name": "Elliott Investment Management", "cik": "0000915220"},
    {"name": "Pershing Square Capital", "cik": "0001336528"},
    {"name": "Third Point LLC", "cik": "0001054023"},
    {"name": "Appaloosa Management", "cik": "0001121846"},
    {"name": "Baupost Group", "cik": "0000925690"},
    {"name": "Lone Pine Capital", "cik": "0001337858"},
    {"name": "Tiger Global Management", "cik": "0001167483"},
    {"name": "Greenlight Capital", "cik": "0001109507"},
    {"name": "Soroban Capital", "cik": "0001516654"},
    {"name": "Viking Global Investors", "cik": "0001103804"},
    {"name": "Coatue Management", "cik": "0001135730"},
]

# CUSIP -> ticker overrides for the most common large caps (EDGAR 13F XML has
# CUSIP + issuer name, but not tickers). Everything else is resolved heuristically.
CUSIP_TICKER_OVERRIDES: dict[str, str] = {
    "037833100": "AAPL",
    "594918104": "MSFT",
    "023135106": "AMZN",
    "88160R101": "TSLA",
    "02079K305": "GOOGL",
    "46647P108": "JPM",
    "30303M102": "META",
    "67066G104": "NVDA",
    "68389X105": "ORCL",
    "458140100": "INTC",
    "92826C839": "V",
    "17275R102": "CRM",
    "00724F101": "ADBE",
    "25470F104": "DIS",
    "023135106": "AMZN",
    "00846U101": "AGNC",
    "58933Y105": "MRK",
    "532457108": "LLY",
    "09062X103": "BIIB",
    "743312100": "PYPL",
}


@dataclass
class FundHolding:
    cusip: str
    issuer: str
    ticker: str
    value_thousands: float
    shares: float
    shares_type: str
    put_call: str
    pct_portfolio: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "cusip": self.cusip,
            "issuer": self.issuer,
            "ticker": self.ticker,
            "value": self.value_thousands,
            "shares": self.shares,
            "shares_type": self.shares_type,
            "put_call": self.put_call,
            "pct_portfolio": round(self.pct_portfolio, 4),
        }


@dataclass
class FundFiling:
    cik: str
    fund_name: str
    form: str
    accession: str
    filing_date: str
    period_of_report: str
    holdings: list[FundHolding] = field(default_factory=list)


@dataclass
class HoldingChange:
    ticker: str
    issuer: str
    cusip: str
    prev_shares: float
    curr_shares: float
    change_shares: float
    change_pct: float
    action: str  # BUY / SELL / NEW / EXITED
    value_thousands: float


def _http_get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": EDGAR_USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _latest_13f_metadata(cik: str, fund_name: str = "") -> dict[str, str] | None:
    """Return the most recent 13F-HR filing metadata for a manager.

    Prefers EDGAR full-text search by fund name (robust against CIK drift),
    falling back to the CIK submissions index.
    """
    if fund_name:
        meta = _search_latest_13f(fund_name)
        if meta:
            return meta
    padded = cik.lstrip("0").zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    try:
        payload = json.loads(_http_get(url))
    except Exception:
        return None
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            return {
                "form": form,
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i] if i < len(recent.get("reportDate", [])) else "",
            }
    return None


def _search_latest_13f(fund_name: str) -> dict[str, str] | None:
    """Find the latest 13F-HR filing for a fund via EDGAR full-text search."""
    import urllib.parse

    query = urllib.parse.urlencode(
        {
            "q": f'"{fund_name.upper()}"',
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": "2020-01-01",
            "enddt": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
    )
    try:
        payload = json.loads(_http_get(f"https://efts.sec.gov/LATEST/search-index?{query}"))
    except Exception as exc:
        logger.warning("13F search failed for %s: %s", fund_name, exc)
        return None
    hits = payload.get("hits", {}).get("hits", [])
    if not hits:
        return None
    # Hits are relevance-ordered; pick the most recent 13F-HR filing.
    best: dict[str, str] | None = None
    for h in hits:
        src = h.get("_source", {})
        if src.get("form") != "13F-HR":
            continue
        adsh = src.get("adsh", "")
        ciks = src.get("ciks", [])
        if not adsh or not ciks:
            continue
        filing_date = src.get("file_date", "")
        if best is None or filing_date > best.get("filing_date", ""):
            best = {
                "form": "13F-HR",
                "accession": adsh,
                "filing_date": filing_date,
                "report_date": src.get("period_ending", ""),
                "cik": ciks[0],
            }
    return best


def _parse_13f_holdings(cik: str, accession: str, holdings_file: str | None = None) -> list[FundHolding]:
    """Fetch and parse the 13F information table XML for a filing."""
    acc_no_dashes = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik.lstrip('0'))}/{acc_no_dashes}"
    if holdings_file:
        url = f"{base}/{holdings_file}"
    else:
        holdings_file = _find_holdings_filename(cik, accession)
        if not holdings_file:
            return []
        url = f"{base}/{holdings_file}"

    text = _http_get(url)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        logger.warning("Failed to parse 13F XML for %s / %s", cik, accession)
        return []

    holdings: list[FundHolding] = []
    for it in root.findall(f".//{{{INFO_TABLE_NS}}}infoTable"):
        def g(tag: str) -> str:
            el = it.find(f".//{{{INFO_TABLE_NS}}}{tag}")
            return el.text.strip() if el is not None and el.text else ""

        issuer = g("nameOfIssuer")
        cusip = g("cusip")
        try:
            # Some filers report `value` in raw dollars, others in thousands.
            value_raw = float(g("value")) if g("value") else 0.0
        except ValueError:
            value_raw = 0.0
        try:
            shares = float(g("sshPrnamt")) if g("sshPrnamt") else 0.0
        except ValueError:
            shares = 0.0
        shares_type = g("sshPrnamtType") or "SH"
        put_call = g("putCall")
        holdings.append(
            FundHolding(
                cusip=cusip,
                issuer=issuer,
                ticker="",
                value_thousands=value_raw,
                shares=shares,
                shares_type=shares_type,
                put_call=put_call,
                pct_portfolio=0.0,
            )
        )

    # Normalize the value unit per filing: infer implied per-share prices and
    # scale thousands-reporting filers up to raw dollars.
    unit_scale = _infer_value_scale(holdings)
    for h in holdings:
        h.value_thousands *= unit_scale

    for h in holdings:
        if not h.ticker:
            h.ticker = CUSIP_TICKER_OVERRIDES.get(h.cusip, "") or _resolve_ticker(h.issuer, h.cusip)

    total_value = sum(h.value_thousands for h in holdings)
    if total_value > 0:
        for h in holdings:
            h.pct_portfolio = h.value_thousands / total_value
    return holdings


def _infer_value_scale(holdings: list[FundHolding]) -> float:
    """Return 1.0 if values are raw dollars, 1000.0 if reported in thousands.

    Uses the median implied per-share price: real stock prices are typically
    under $2,000; if the median price is absurdly low (< $1), the values are
    reported in thousands and need scaling by 1000.
    """
    prices: list[float] = []
    for h in holdings:
        if h.shares > 0 and h.value_thousands > 0:
            prices.append(h.value_thousands / h.shares)
    if not prices:
        return 1.0
    prices.sort()
    median = prices[len(prices) // 2]
    if median < 1.0:
        return 1000.0
    return 1.0


_TICKER_CACHE: dict[str, str] = {}

# Load the app's company_tickers.json (ticker -> [names]) once.
_COMPANY_TICKERS: dict[str, list[str]] = {}
try:
    from .config import settings

    _path = Path(__file__).resolve().parent / "data" / "company_tickers.json"
    if _path.exists():
        _COMPANY_TICKERS = json.loads(_path.read_text(encoding="utf-8"))
except Exception:
    _COMPANY_TICKERS = {}


def _resolve_ticker(issuer: str, cusip: str) -> str:
    """Resolve a ticker from issuer name using company_tickers.json + heuristics."""
    if issuer in _TICKER_CACHE:
        return _TICKER_CACHE[issuer]
    norm = re.sub(r"[^A-Z0-9]", "", issuer.upper())
    ticker = ""
    for sym, names in _COMPANY_TICKERS.items():
        for n in names:
            if re.sub(r"[^A-Z0-9]", "", n.upper()) == norm or norm in re.sub(r"[^A-Z0-9]", "", n.upper()):
                ticker = sym
                break
        if ticker:
            break
    if not ticker:
        # Try "NAME (TICKER)" patterns in the issuer string.
        m = re.search(r"\(([A-Z]{1,5})\)\s*$", issuer)
        if m:
            ticker = m.group(1)
    _TICKER_CACHE[issuer] = ticker
    return ticker


def _find_holdings_filename(cik: str, accession: str) -> str | None:
    acc_no_dashes = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik.lstrip('0'))}/{acc_no_dashes}"
    try:
        index = json.loads(_http_get(f"{base}/index.json"))
        items = index.get("directory", {}).get("item", [])
        for it in items:
            name = it["name"].lower()
            if name.endswith(".xml") and "primary" not in name and "xsl" not in name:
                return it["name"]
        for it in items:
            name = it["name"].lower()
            if name.endswith(".xml"):
                return it["name"]
    except Exception as exc:
        logger.warning("Could not list filing %s files: %s", accession, exc)
    return None


def fetch_latest_filings(funds: list[dict[str, str]] | None = None, max_holdings: int = 500) -> list[FundFiling]:
    """Fetch the latest 13F filing for each fund."""
    funds = funds or MAJOR_FUNDS
    out: list[FundFiling] = []
    for fund in funds:
        cik = fund["cik"]
        try:
            meta = _latest_13f_metadata(cik, fund["name"])
        except Exception as exc:
            logger.warning("Failed to get metadata for %s: %s", fund["name"], exc)
            continue
        if not meta:
            logger.warning("No 13F filing found for %s", fund["name"])
            continue
        # The search may have found a newer/different CIK for the fund.
        if meta.get("cik"):
            cik = meta["cik"]
        holdings_file = None
        try:
            holdings_file = _find_holdings_filename(cik, meta["accession"])
        except Exception:
            pass
        try:
            holdings = _parse_13f_holdings(cik, meta["accession"], holdings_file)
        except Exception as exc:
            logger.warning("Failed to parse holdings for %s: %s", fund["name"], exc)
            holdings = []
        out.append(
            FundFiling(
                cik=cik,
                fund_name=fund["name"],
                form=meta["form"],
                accession=meta["accession"],
                filing_date=meta["filing_date"],
                period_of_report=meta.get("report_date", ""),
                holdings=holdings[:max_holdings],
            )
        )
        logger.info("Fetched %s: %d holdings (filed %s)", fund["name"], len(holdings), meta["filing_date"])
    return out


def compute_quarterly_changes(cik: str, db: Database) -> list[HoldingChange]:
    """Compare the latest two stored filings for a fund and produce BUY/SELL/ NEW/EXITED."""
    filings = db.fund_filings(cik=cik, limit=2)
    if len(filings) < 2:
        return []
    prev = {h["cusip"]: h for h in db.fund_holdings(filings[1]["id"])}
    curr = {h["cusip"]: h for h in db.fund_holdings(filings[0]["id"])}

    changes: list[HoldingChange] = []
    for cusip, h in curr.items():
        p = prev.get(cusip)
        prev_shares = p["shares"] if p else 0.0
        curr_shares = h["shares"]
        if prev_shares == 0 and curr_shares > 0:
            action = "NEW"
        elif prev_shares > 0 and curr_shares == 0:
            action = "EXITED"
        elif prev_shares > 0:
            change_pct = (curr_shares - prev_shares) / abs(prev_shares)
            action = "BUY" if change_pct >= 0.02 else ("SELL" if change_pct <= -0.02 else "HOLD")
        else:
            action = "HOLD"
        changes.append(
            HoldingChange(
                ticker=h["ticker"],
                issuer=h["issuer"],
                cusip=cusip,
                prev_shares=prev_shares,
                curr_shares=curr_shares,
                change_shares=curr_shares - prev_shares,
                change_pct=((curr_shares - prev_shares) / abs(prev_shares)) if prev_shares else 0.0,
                action=action,
                value_thousands=h["value_thousands"],
            )
        )
    changes.sort(key=lambda c: c.value_thousands, reverse=True)
    return changes


def run_institutional_fetch(db_path: str | None = None) -> list[FundFiling]:
    """Fetch latest 13F filings for all major funds and store them in the DB."""
    db = Database(db_path or settings.db_path)
    db.init_schema()
    filings = fetch_latest_filings()
    for f in filings:
        filing_id = db.upsert_fund_filing(
            cik=f.cik,
            fund_name=f.fund_name,
            form=f.form,
            accession=f.accession,
            filing_date=f.filing_date,
            period_of_report=f.period_of_report,
        )
        db.replace_fund_holdings(
            filing_id,
            [h.as_dict() for h in f.holdings],
        )
        # Store the previous quarter filing too, so buy/sell deltas work right away.
        prev = _previous_filing(f)
        if prev:
            prev_id = db.upsert_fund_filing(
                cik=prev.cik,
                fund_name=prev.fund_name,
                form=prev.form,
                accession=prev.accession,
                filing_date=prev.filing_date,
                period_of_report=prev.period_of_report,
            )
            db.replace_fund_holdings(prev_id, [h.as_dict() for h in prev.holdings])
    return filings


def _previous_filing(current: FundFiling, max_holdings: int = 500) -> FundFiling | None:
    """Find the 13F filing immediately before the current one for the same fund.

    Uses the fund's own CIK submissions index (reliable) rather than full-text
    search, which can match a differently-named entity that shares a keyword.
    """
    padded = current.cik.lstrip("0").zfill(10)
    try:
        payload = json.loads(_http_get(f"https://data.sec.gov/submissions/CIK{padded}.json"))
    except Exception:
        return None
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    prev: dict[str, str] | None = None
    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            acc = recent["accessionNumber"][i]
            if acc == current.accession:
                continue
            if prev is None or recent["filingDate"][i] > prev.get("filing_date", ""):
                prev = {
                    "form": form,
                    "accession": acc,
                    "filing_date": recent["filingDate"][i],
                    "report_date": recent["reportDate"][i] if i < len(recent.get("reportDate", [])) else "",
                }
    if not prev:
        return None
    holdings_file = _find_holdings_filename(current.cik, prev["accession"])
    try:
        holdings = _parse_13f_holdings(current.cik, prev["accession"], holdings_file)
    except Exception as exc:
        logger.warning("Failed to parse previous holdings for %s: %s", current.fund_name, exc)
        holdings = []
    return FundFiling(
        cik=current.cik,
        fund_name=current.fund_name,
        form=prev["form"],
        accession=prev["accession"],
        filing_date=prev["filing_date"],
        period_of_report=prev.get("report_date", ""),
        holdings=holdings[:max_holdings],
    )


def fund_summaries(db: Database) -> list[dict[str, Any]]:
    """Latest per-fund summary: filing date, top holdings, buy/sell actions."""
    # Keep the most recent filing per fund name (funds can drift across CIKs).
    filings = sorted(db.fund_filings(limit=500), key=lambda f: f["filing_date"], reverse=True)
    latest_by_name: dict[str, dict[str, Any]] = {}
    for filing in filings:
        name = filing["fund_name"]
        if name not in latest_by_name:
            latest_by_name[name] = filing

    summaries: list[dict[str, Any]] = []
    for name, filing in latest_by_name.items():
        holdings = db.fund_holdings(filing["id"], limit=15)
        changes = compute_quarterly_changes(filing["cik"], db)
        top_actions = [c for c in changes if c.action in ("BUY", "SELL", "NEW", "EXITED")][:12]
        summaries.append(
            {
                "cik": filing["cik"],
                "fund": filing["fund_name"],
                "form": filing["form"],
                "filing_date": filing["filing_date"],
                "period_of_report": filing["period_of_report"],
                "total_holdings": len(holdings),
                "top_holdings": [
                    {
                        "cusip": h["cusip"],
                        "issuer": h["issuer"],
                        "ticker": h["ticker"],
                        "value": h["value_thousands"],
                        "shares": h["shares"],
                        "pct_portfolio": h["pct_portfolio"],
                    }
                    for h in holdings
                ],
                "changes": [
                    {
                        "ticker": c.ticker,
                        "issuer": c.issuer,
                        "action": c.action,
                        "change_shares": c.change_shares,
                        "change_pct": round(c.change_pct, 4),
                        "value": c.value_thousands,
                    }
                    for c in top_actions
                ],
            }
        )
    return summaries
