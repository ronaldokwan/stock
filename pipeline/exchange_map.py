"""Map a holding's local currency to candidate Yahoo Finance ticker suffixes.

The SSGA holdings file gives us a local ticker and a local currency but no
exchange. For most currencies that is a one-to-one mapping to a Yahoo suffix.
For a few (EUR above all) it is ambiguous, so we emit an ordered candidate list
and let ``universe.resolve`` probe Yahoo for the one that actually exists.
"""
from __future__ import annotations

# Ordered by descending likelihood so the first probe usually wins.
CURRENCY_SUFFIXES: dict[str, list[str]] = {
    "USD": [""],
    "EUR": [".PA", ".DE", ".AS", ".MI", ".MC", ".BR", ".HE", ".LS", ".VI", ".IR"],
    "JPY": [".T"],
    "GBP": [".L"],
    "GBp": [".L"],
    "CAD": [".TO", ".V"],
    "HKD": [".HK"],
    "AUD": [".AX"],
    "TWD": [".TW", ".TWO"],
    "INR": [".NS", ".BO"],
    "KRW": [".KS", ".KQ"],
    "CHF": [".SW"],
    "SEK": [".ST"],
    "DKK": [".CO"],
    "NOK": [".OL"],
    "ZAR": [".JO"],
    "SAR": [".SR"],
    "CNY": [".SS", ".SZ"],
    "SGD": [".SI"],
    "MXN": [".MX"],
    # Yahoo serves the UAE exchanges under .AE; .AD/.DU return nothing.
    "AED": [".AE", ".AD", ".DU"],
    "IDR": [".JK"],
    "ILS": [".TA"],
    "BRL": [".SA"],
    "PLN": [".WA"],
    "THB": [".BK"],
    "HUF": [".BD"],
    "KWD": [".KW"],
    "TRY": [".IS"],
    "MYR": [".KL"],
    "NZD": [".NZ"],
    "CLP": [".SN"],
    "PHP": [".PS"],
    "CZK": [".PR"],
    "EGP": [".CA"],
    "QAR": [".QA"],
    "COP": [".CL"],
    "PEN": [".LM"],
    "GRD": [".AT"],
}

# Yahoo uses a dash where US tickers use a dot for share classes (BRK.B -> BRK-B).
def _normalise_us(ticker: str) -> str:
    return ticker.replace(".", "-")


def _foreign_bases(ticker: str) -> list[str]:
    """Candidate base symbols for a non-US listing.

    Share-class notation is where these go wrong. The holdings file writes BAE
    Systems as ``BA.`` (a trailing dot), Atlas Copco as ``ATCO B`` (a space) and
    Novo Nordisk as ``NOVOB`` (nothing at all), while Yahoo wants ``BA``,
    ``ATCO-B`` and ``NOVO-B``. Rather than guess, emit each plausible form and
    let the tiered probe decide which one actually exists.
    """
    t = ticker.strip()
    # SSGA occasionally appends the trading currency to the ticker
    # ("SECU B_SEK"); Yahoo wants only the part before the underscore.
    t = t.split("_", 1)[0].strip()
    bases: list[str] = []

    def add(value: str) -> None:
        value = value.strip("-").strip()
        if value and value not in bases:
            bases.append(value)

    stripped = t.rstrip(".")                 # "BA." -> "BA"
    add(stripped.replace(" ", "-").replace(".", "-"))
    add(stripped.replace(" ", "").replace(".", ""))

    # A trailing single letter with no separator is usually a share class.
    compact = stripped.replace(" ", "").replace(".", "")
    if len(compact) >= 4 and compact[-1] in "ABCD" and compact[-2].isalpha():
        add(f"{compact[:-1]}-{compact[-1]}")
    return bases


def candidates(ticker: str, currency: str) -> list[str]:
    """Return ordered Yahoo symbol candidates for a local ticker + currency."""
    t = str(ticker).strip().upper()
    cur = str(currency).strip()
    if not t or t == "-":
        return []

    # SSGA prefixes Korean codes with "A" (A000660); Yahoo wants the bare code.
    if cur == "KRW" and len(t) == 7 and t.startswith("A") and t[1:].isdigit():
        t = t[1:]

    suffixes = CURRENCY_SUFFIXES.get(cur) or CURRENCY_SUFFIXES.get(cur.upper())
    if suffixes is None:
        return []

    out: list[str] = []
    for suf in suffixes:
        if suf == "":                       # US listing
            bases = [_normalise_us(t)]
        elif suf == ".HK":                  # Yahoo zero-pads HK to 4 digits
            bases = [t.zfill(4) if t.isdigit() else t]
        elif suf in (".SS", ".SZ", ".KS", ".KQ"):   # China / Korea are 6 digits
            bases = [t.zfill(6) if t.isdigit() else t]
        elif suf == ".T":                   # Tokyo codes are 4 chars
            bases = [t.zfill(4) if t.isdigit() else t]
        else:
            bases = _foreign_bases(t)
        for base in bases:
            symbol = f"{base}{suf}"
            if symbol not in out:
                out.append(symbol)
    return out


def is_ambiguous(currency: str) -> bool:
    return len(CURRENCY_SUFFIXES.get(str(currency).strip(), [])) > 1
