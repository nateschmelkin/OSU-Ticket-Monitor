import re
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any

HEADERS_BASE = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9," "*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}


def fetch_event_page(url: str, user_agent: str = "MaizeTixMonitor/1.0") -> str:
    headers = dict(HEADERS_BASE)
    headers["User-Agent"] = user_agent
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


_MONEY_RE = re.compile(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)")


def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    m = _MONEY_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def parse_prices_summary(html: str) -> Dict[str, Any]:
    """
    Returns:
      {
        "lowest_price": float|None,        # from "Lowest Price" summary box
        "median_sale": float|None,         # from "Median Sale" summary box
        "computed_min_price": float|None,  # min from listing rows
        "num_listings": int|None,
        "all_prices": list[float],  # all individual ticket prices
      }
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1) Parse summary blocks (Lowest Price, Median Sale)
    lowest_price = None
    median_sale = None

    for lbl in soup.find_all(text=re.compile(r"Lowest\s*Price", re.I)):
        parent = lbl.parent
        if parent:
            texts = " ".join(parent.get_text(" ", strip=True).split())
            maybe = _parse_money(texts)
            if maybe is not None:
                lowest_price = maybe
                break

    if lowest_price is None:
        m = _MONEY_RE.search(soup.get_text(" ", strip=True))
        lowest_price = None if not m else float(m.group(1))

    for lbl in soup.find_all(text=re.compile(r"Median\s*Sale", re.I)):
        parent = lbl.parent
        if parent:
            texts = " ".join(parent.get_text(" ", strip=True).split())
            maybe = _parse_money(texts)
            if maybe is not None:
                median_sale = maybe
                break

    # 2) Parse listing rows prices
    prices = []
    rows = soup.select("table tr")  # adjust selector if site structure changes
    for tr in rows:
        if not tr.find(text=re.compile(r"\bBuy\b", re.I)):
            continue

        td_texts = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        for td in td_texts:
            price = _parse_money(td)
            if price:
                prices.append(price)
                break  # only take the first price per row

    computed_min_price = min(prices) if prices else None
    num_listings = len(prices) if prices else None

    return {
        "lowest_price": lowest_price,
        "median_sale": median_sale,
        "computed_min_price": computed_min_price,
        "num_listings": num_listings,
        "all_prices": sorted(prices) if prices else [],  # sorted prices
    }
