"""Stage 1: build the top-N global universe and resolve Yahoo Finance tickers."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd
import requests
import yaml

from . import config as C
from .exchange_map import candidates, is_ambiguous

log = logging.getLogger(__name__)

OVERRIDES = Path(__file__).parent / "overrides.yaml"
SUPPLEMENTAL = Path(__file__).parent / "supplemental.yaml"
UNIVERSE_JSON = C.CACHE / "universe.json"
UNRESOLVED_JSON = C.DATA / "unresolved.json"
MISMATCHES_JSON = C.DATA / "mismatches.json"


def download_holdings(force: bool = False) -> Path:
    """Fetch the SSGA SPGM daily holdings file, falling back to the seed copy."""
    if C.SEED_HOLDINGS.exists() and not force:
        log.info("using cached holdings file %s", C.SEED_HOLDINGS.name)
        return C.SEED_HOLDINGS
    try:
        r = requests.get(C.SPGM_URL, headers={"User-Agent": C.BROWSER_UA}, timeout=60)
        r.raise_for_status()
        if not r.content.startswith(b"PK"):        # xlsx is a zip
            raise ValueError("response was not an xlsx file")
        C.SEED_HOLDINGS.write_bytes(r.content)
        log.info("downloaded holdings (%d KB)", len(r.content) // 1024)
    except Exception as e:                          # noqa: BLE001
        if C.SEED_HOLDINGS.exists():
            log.warning("holdings download failed (%s); using committed seed", e)
        else:
            raise
    return C.SEED_HOLDINGS


def load_holdings(path: Path) -> pd.DataFrame:
    """Parse the holdings sheet into a clean, weight-ranked equity frame."""
    header_row = _find_header_row(path)
    df = pd.read_excel(path, header=header_row)
    df.columns = [str(c).strip() for c in df.columns]

    df = df.rename(columns={"Local Currency": "currency", "Ticker": "local_ticker",
                            "Name": "name", "Weight": "weight", "SEDOL": "sedol"})
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df["local_ticker"] = df["local_ticker"].astype(str).str.strip()

    df = df[
        df["local_ticker"].notna()
        & (df["local_ticker"] != "-")
        & (df["local_ticker"].str.len() > 0)
        & (df["local_ticker"].str.lower() != "nan")
        & df["weight"].notna()
        & (df["weight"] > 0)
        & df["currency"].notna()
    ]
    # Cash, futures and FX lines carry no usable ticker; drop any leftovers.
    df = df[~df["name"].astype(str).str.contains("CASH|FUTURE|USD X|NET OTHER",
                                                 case=False, na=False)]
    df = df.sort_values("weight", ascending=False).reset_index(drop=True)
    log.info("parsed %d equity holdings", len(df))
    return df


def _find_header_row(path: Path) -> int:
    """SSGA prepends a few metadata rows; locate the real header."""
    probe = pd.read_excel(path, header=None, nrows=15)
    for i, row in probe.iterrows():
        vals = {str(v).strip() for v in row.tolist()}
        if {"Name", "Ticker", "Weight"}.issubset(vals):
            return int(i)
    raise ValueError(f"could not locate header row in {path.name}")


def _load_overrides() -> dict[str, str]:
    if not OVERRIDES.exists():
        return {}
    data = yaml.safe_load(OVERRIDES.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("tickers") or {}).items()}


# Corporate boilerplate carries no identifying information, and share-class and
# depositary wording differs between SSGA's name and Yahoo's for the same company.
_NAME_NOISE = {
    "inc", "corp", "corporation", "ltd", "limited", "plc", "llc", "lp",
    "company", "companies", "group", "holding", "holdings", "the", "and",
    "class", "reg", "shs", "ord", "adr", "gdr", "spon", "sponsored", "new",
    "pref", "prf", "cdi", "cdr", "receipts", "nyrt", "pjsc", "jsc",
    "psc", "tbk", "bhd", "oyj", "asa", "spa", "sab", "sac", "aktiengesellschaft",
    "société", "societe", "anonyme", "public",
}
# SSGA truncates its names to 29 characters, always at the end, so the holding's
# LAST word may be a fragment: "TAIWAN SEMICONDUCTOR MANUFAC" has to pair with
# Yahoo's "Manufacturing". Only that final word gets prefix tolerance -- allowing
# it anywhere pairs "SAMSUNG ELECTRONICS" with "Samsung Electro-Mechanics".
_PREFIX_MIN = 4


def _name_words(name) -> list[str]:
    """Identifying words in a company name, in order, accent- and case-folded."""
    folded = (unicodedata.normalize("NFKD", str(name))
              .encode("ascii", "ignore").decode("ascii"))
    return [t for t in re.findall(r"[a-z0-9]+", folded.lower())
            if len(t) > 2 and t not in _NAME_NOISE]


def _name_tokens(name) -> set[str]:
    return set(_name_words(name))


def _same_company(holding_name, yahoo_name) -> bool:
    """Whether two names plausibly describe the same company.

    Used only to *choose between* candidate symbols, never to reject one
    outright. Companies rename themselves -- General Electric answers to "GE
    Aerospace" and DHL Group to "Deutsche Post AG" -- so a name disagreement is
    weak evidence on its own, and treating it as fatal would drop good rows.

    Agreement, on the other hand, is strong evidence, and it is what
    distinguishes Banco Santander from Sanofi when both are quoted as ``SAN`` in
    euros. One shared word is enough only when that is all either name has;
    otherwise "China Mobile" would answer to "China Life".
    """
    words = _name_words(holding_name)
    a, b = set(words), _name_tokens(yahoo_name)
    if not a or not b:
        return False

    shared = len(a & b)
    # The holding's final word may be a truncation of a longer one.
    tail = words[-1] if words else ""
    if tail and tail not in b and len(tail) >= _PREFIX_MIN:
        shared += any(y.startswith(tail) for y in b)

    # One shared word suffices only when it is the whole of both names -- Linde
    # is "Linde plc" on both sides. If either name has more to say, that has to
    # agree too: "MERCK KGAA" shares "merck" with "Merck & Co., Inc." and is a
    # different company.
    return shared >= 2 or (shared >= 1 and len(a) == 1 and len(b) == 1)


def resolve(df: pd.DataFrame, probe_fn, limit: int | None = None) -> pd.DataFrame:
    """Attach a validated Yahoo symbol to each holding.

    ``probe_fn(symbols) -> {symbol: company name}`` for the symbols Yahoo serves.
    The name is what makes this resolution rather than guessing: a ticker plus a
    currency is not unique. ``SAN`` in euros is both Banco Santander in Madrid
    and Sanofi in Paris, and accepting the first suffix that merely *returned
    data* silently published Sanofi under Santander's index weight while dropping
    Santander from the table entirely.
    """
    overrides = _load_overrides()
    n = limit or C.RESOLVE_BUFFER
    work = df.head(n).copy()

    # Build the full candidate matrix up front so we can probe in one pass.
    work["candidates"] = [
        candidates(t, c) for t, c in zip(work["local_ticker"], work["currency"])
    ]
    # An override exists precisely because the holding is a depositary receipt
    # or an odd local code that points at a real primary listing. Flag those so
    # deduplication does not later demote them for looking like a GDR line.
    work["via_override"] = False
    for i, row in work.iterrows():
        key = f"{row['local_ticker']}:{row['currency']}"
        if key in overrides:
            work.at[i, "candidates"] = [overrides[key]]
            work.at[i, "via_override"] = True
        elif row["local_ticker"] in overrides:
            work.at[i, "candidates"] = [overrides[row["local_ticker"]]]
            work.at[i, "via_override"] = True

    # Only genuinely ambiguous tickers are worth a network probe. A holding with
    # a single candidate is accepted provisionally -- the history fetch validates
    # it for free, and probing all of them wastes thousands of requests against a
    # source that rate-limits hard.
    #
    # Ambiguous ones are probed in priority tiers: every holding's first choice
    # goes in one batch, and only those still unresolved contribute a second
    # choice, and so on. Since the candidate lists are ordered by likelihood,
    # nearly everything resolves in the first tier.
    single = sum(1 for lst in work["candidates"] if len(lst) == 1)
    pending = {i: lst for i, lst in work["candidates"].items() if len(lst) > 1}
    log.info("resolving %d holdings: %d unambiguous, %d ambiguous",
             len(work), single, len(pending))

    # Two outcomes per holding: a candidate whose name agrees with the holding
    # (accepted, and probing stops there), or one that merely exists (kept as a
    # fallback, and probing continues in case a later tier agrees). Only if no
    # tier agrees does the fallback win -- which is the old behaviour, so a
    # renamed company is never dropped for failing to match.
    resolved: dict[int, str] = {}
    fallback: dict[int, tuple[str, str]] = {}
    probed_total = 0
    for tier in range(max((len(v) for v in pending.values()), default=0)):
        batch = {i: lst[tier] for i, lst in pending.items()
                 if i not in resolved and tier < len(lst)}
        if not batch:
            break
        found = probe_fn(sorted(set(batch.values())))
        probed_total += len(set(batch.values()))
        for i, sym in batch.items():
            if sym not in found:
                continue
            if _same_company(work.at[i, "name"], found[sym]):
                resolved[i] = sym
            elif i not in fallback:
                fallback[i] = (sym, found[sym])
        log.info("  tier %d: %d probed, %d/%d ambiguous resolved",
                 tier + 1, len(set(batch.values())), len(resolved), len(pending))
    log.info("probe requests used: %d", probed_total)

    corrected = [i for i in resolved if i in fallback and fallback[i][0] != resolved[i]]
    for i in corrected:
        log.info("  %s/%s -> %s (not %s, which is %s)",
                 work.at[i, "local_ticker"], work.at[i, "currency"], resolved[i],
                 fallback[i][0], fallback[i][1])
    if corrected:
        log.info("name check corrected %d ticker collisions", len(corrected))

    symbols, unresolved, unverified = [], [], []
    for i, row in work.iterrows():
        options = row["candidates"]
        if len(options) == 1:
            hit = options[0]
        else:
            hit = resolved.get(i) or (fallback[i][0] if i in fallback else None)
            if hit is not None and i not in resolved:
                unverified.append({
                    "name": row["name"], "local_ticker": row["local_ticker"],
                    "currency": row["currency"], "symbol": hit,
                    "yahoo_name": fallback[i][1],
                    "weight": float(row["weight"]),
                })
        symbols.append(hit)
        if hit is None:
            unresolved.append({
                "name": row["name"], "local_ticker": row["local_ticker"],
                "currency": row["currency"], "weight": float(row["weight"]),
                "tried": row["candidates"],
            })
    work["symbol"] = symbols

    UNRESOLVED_JSON.write_text(json.dumps(unresolved, indent=2), encoding="utf-8")
    if unresolved:
        log.warning("%d holdings unresolved -> %s (add fixes to overrides.yaml)",
                    len(unresolved), UNRESOLVED_JSON.name)

    # Accepted on existence alone. Usually a rename, occasionally a wrong
    # company -- either way it is the worklist for overrides.yaml.
    unverified.sort(key=lambda r: -r["weight"])
    MISMATCHES_JSON.write_text(json.dumps(unverified, indent=2), encoding="utf-8")
    if unverified:
        log.warning("%d holdings resolved without a name match -> %s "
                    "(review; most are renames)", len(unverified),
                    MISMATCHES_JSON.name)

    out = work[work["symbol"].notna()].drop(columns=["candidates"])
    out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
    log.info("resolved universe: %d candidate symbols", len(out))
    return out


def load_supplemental() -> pd.DataFrame:
    """Curated constituents the holdings file does not contain.

    The universe is an ETF's *holdings*, not its index's constituent list. SPGM
    samples: it holds ~2,900 of the index's names, and weights are free-float
    adjusted, so a company with a small float can be absent at any weight.
    Saudi Aramco -- ninth largest on earth, ~2% float -- is not in the file at
    all, and no amount of ticker resolution can find something that is not there.

    Entries arrive with a Yahoo symbol already known, so they skip resolution,
    and with no weight, since the fund holds none of them.
    """
    if not SUPPLEMENTAL.exists():
        return pd.DataFrame()
    data = yaml.safe_load(SUPPLEMENTAL.read_text(encoding="utf-8")) or {}
    entries = data.get("companies") or []
    if not entries:
        return pd.DataFrame()
    return pd.DataFrame([{
        "name": e["name"],
        "local_ticker": e.get("local_ticker") or e["symbol"],
        "sedol": None,
        # No index weight: the fund does not hold these. NaN rather than 0.0 so
        # the published index_weight is null rather than a fabricated zero.
        "weight": float("nan"),
        "currency": e.get("currency"),
        "symbol": e["symbol"],
        # Their names are the primary listing's, never a GDR line's wording, so
        # deduplication must not demote them on a name match.
        "via_override": True,
        "supplemental": True,
    } for e in entries])


def with_supplemental(df: pd.DataFrame) -> pd.DataFrame:
    """Append curated constituents to the resolved candidates.

    Appended before quotes and history are fetched, so a supplemental row is
    filled in from exactly the same sources as every other row. A symbol already
    resolved from the holdings file wins: if the fund starts holding one of
    these, the real holding (with its real weight) supersedes the manual entry.
    """
    df = df.copy()
    df["supplemental"] = False
    extra = load_supplemental()
    if extra.empty:
        return df

    already = set(df["symbol"])
    extra = extra[~extra["symbol"].isin(already)]
    if extra.empty:
        log.info("supplemental: all %d entries already in the holdings file",
                 len(load_supplemental()))
        return df

    log.info("supplemental: added %d curated symbols not in the holdings file (%s)",
             len(extra), ", ".join(extra["symbol"]))
    return pd.concat([df, extra], ignore_index=True)


# A holding whose SSGA name carries one of these is a secondary line: a
# depositary receipt over shares that trade somewhere else, an Australian CDI,
# or a preferred class. When the same company also appears via its primary
# listing, that primary line is the one a market-cap screener should show.
_DR_MARKERS = re.compile(r"\b(ADR|GDR|CDI|CDR|REG\s*S|RECEIPTS?)\b", re.I)
_PREF_MARKERS = re.compile(r"\b(PREF|PRF)\b", re.I)


def _company_key(name: str) -> str:
    """Normalise a Yahoo long name into a company identity key.

    Yahoo returns the *issuer* name on every line of a company, so the ADR and
    the local listing both come back as "Alibaba Group Holding Limited". Exact
    match after case-folding and stripping punctuation is therefore enough to
    pair them, and is far safer than fuzzy matching -- which would happily merge
    Samsung Electronics with Samsung Electro-Mechanics.
    """
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def deduplicate(df: pd.DataFrame, names: dict[str, str]) -> pd.DataFrame:
    """Collapse multiple listings of one company into a single row.

    The index holds several companies more than once -- Alphabet's two share
    classes, TSMC's Taiwan line plus its ADR, Rio Tinto's dual-listed pair. Yahoo
    reports whole-company market cap on each of those lines, so leaving them all
    in double-counts the company and lets it occupy two ranks at once.

    The surviving line is chosen by preferring an ordinary share over a
    preferred, a primary listing over a depositary receipt, and then the larger
    index weight. Weights of the merged lines are summed, since together they are
    what the index actually holds in that company.
    """
    if not names:
        return df

    keys, is_dr, is_pref = [], [], []
    for _, row in df.iterrows():
        long_name = names.get(row["symbol"])
        keys.append(_company_key(long_name) if long_name else None)
        holding_name = str(row.get("name", ""))
        # An overridden holding has been redirected to the primary listing, so
        # the GDR wording left in its SSGA name no longer describes the symbol.
        override = bool(row.get("via_override"))
        is_dr.append(bool(_DR_MARKERS.search(holding_name)) and not override)
        is_pref.append(bool(_PREF_MARKERS.search(holding_name)) and not override)

    work = df.copy()
    work["_key"], work["_dr"], work["_pref"] = keys, is_dr, is_pref

    keep_idx, merged = [], {}
    for key, grp in work.groupby("_key", sort=False):
        if len(grp) == 1:
            keep_idx.append(grp.index[0])
            continue
        ordered = grp.sort_values(["_pref", "_dr", "weight"],
                                  ascending=[True, True, False])
        winner = ordered.index[0]
        losers = list(ordered.index[1:])
        keep_idx.append(winner)
        merged[winner] = list(work.loc[losers, "symbol"])
        work.at[winner, "weight"] = float(grp["weight"].sum())
        log.info("  merged %s -> %s (%s)", ", ".join(work.loc[losers, "symbol"]),
                 work.at[winner, "symbol"], str(work.at[winner, "name"])[:34])

    unkeyed = work[work["_key"].isna()].index          # no Yahoo name: never merge
    keep = sorted(set(keep_idx) | set(unkeyed))
    out = work.loc[keep].copy()
    out["merged_symbols"] = [merged.get(i) for i in out.index]
    out = out.drop(columns=["_key", "_dr", "_pref"])
    dropped = len(df) - len(out)
    log.info("deduplicated %d duplicate listings -> %d unique companies",
             dropped, len(out))
    # Summed weights can change the ordering, so re-rank before truncating.
    return out.sort_values("weight", ascending=False).reset_index(drop=True)


def finalise(df: pd.DataFrame, with_data: set[str], target: int,
             names: dict[str, str] | None = None) -> pd.DataFrame:
    """Keep the largest ``target`` holdings that actually returned data.

    Resolution deliberately runs against a buffer above the target so that
    symbols which turn out to be dead (bad suffix guess, delisted, no Yahoo
    coverage) can be dropped without the table falling short of 1000 rows, and
    so that collapsing duplicate listings pulls real companies in behind them.

    Supplemental rows survive the truncation unconditionally: they carry no index
    weight, so a weight-ordered cut would always discard them, which would defeat
    the entire point of listing them. They take slots from the bottom of the
    weighted set instead.
    """
    kept = df[df["symbol"].isin(with_data)].reset_index(drop=True)
    dropped = len(df) - len(kept)
    if dropped:
        log.info("dropped %d symbols that returned no data", dropped)

    if "supplemental" in df.columns:
        lost = df[df["supplemental"].fillna(False).astype(bool)
                  & ~df["symbol"].isin(with_data)]
        for _, row in lost.iterrows():
            log.warning("supplemental symbol %s returned no data - check "
                        "supplemental.yaml", row["symbol"])

    kept = deduplicate(kept, names or {})

    flag = (kept["supplemental"].fillna(False).astype(bool)
            if "supplemental" in kept.columns else pd.Series(False, index=kept.index))
    extra, indexed = kept[flag], kept[~flag]
    room = max(target - len(extra), 0)
    out = pd.concat([indexed.head(room), extra], ignore_index=True)

    if len(extra):
        log.info("final universe: %d symbols (%d from the index, %d supplemental)",
                 len(out), min(len(indexed), room), len(extra))
    else:
        log.info("final universe: %d symbols", len(out))
    return out


def save(df: pd.DataFrame) -> None:
    df.to_json(UNIVERSE_JSON, orient="records", indent=2)


def load_saved() -> pd.DataFrame:
    return pd.read_json(UNIVERSE_JSON)
