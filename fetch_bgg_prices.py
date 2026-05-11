#!/usr/bin/env python3
"""
Pobiera najnizsze ceny gier z BoardGamePrices.co.uk dla listy bgg_id z geeklist JSON.

Konfiguracja:
  - sitename: domena, ktora linkuje do BGP (wymagana przez TOS API).
  - currency=PLN, destination=PL, sort=CHEAP2 (najtansze po cenie produktu).
  - Wybiera najtansza oferte z stock=Y (preferujac dostepne), fallback: najtansza w ogole.
  - Cache: jesli prices.json mlodszy niz --max-age-hours, nie odpytuje (TOS API: min 1h).

Wynik: prices.json z mapa bggId -> {minProduct, url, offers, inStock, stock}.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BGP_INFO_URL = "https://boardgameprices.co.uk/api/info"

DEFAULT_BATCH_SIZE = 30
DEFAULT_BATCH_PAUSE_SEC = 1.0
DEFAULT_MAX_AGE_HOURS = 12.0
DEFAULT_SITENAME = "pczarnopys.github.io"
DEFAULT_CURRENCY = "PLN"
DEFAULT_DESTINATION = "PL"
DEFAULT_TIMEOUT_SEC = 60.0
DEFAULT_RETRIES = 3
DEFAULT_RETRY_PAUSE_SEC = 5.0


def now_utc_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(s: str) -> dt.datetime | None:
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return dt.datetime.fromisoformat(s).astimezone(dt.UTC)
    except (ValueError, TypeError):
        return None


def http_get_json(url: str, timeout: float, retries: int, pause: float) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MHLV-prices/1.0 (+https://pczarnopys.github.io/MHLV/)",
                "Accept": "application/json, */*",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = resp.getcode()
                if code != 200:
                    raise RuntimeError(f"HTTP {code}")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(pause * (attempt + 1))
                last_err = e
                continue
            raise
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as e:
            if attempt < retries:
                time.sleep(pause * (attempt + 1))
                last_err = e
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("Nieosiagalne: brak odpowiedzi i brak bledu.")


def chunked(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def pick_best_offer(prices: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, int, int]:
    """Wybiera najtansza po `product` z stock=Y (preferowane), fallback: najtansza w ogole.

    Zwraca (oferta_lub_None, total_offers, in_stock_count).
    """
    if not prices:
        return None, 0, 0
    in_stock = [p for p in prices if str(p.get("stock", "")).upper() == "Y"]
    pool = in_stock if in_stock else prices

    def product_value(p: dict[str, Any]) -> float:
        try:
            return float(p.get("product"))
        except (TypeError, ValueError):
            return float("inf")

    best = min(pool, key=product_value)
    if product_value(best) == float("inf"):
        return None, len(prices), len(in_stock)
    return best, len(prices), len(in_stock)


def collect_bgg_ids(geeklist_path: Path) -> list[str]:
    rows = json.loads(geeklist_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    ordered: list[str] = []
    for r in rows:
        bid = r.get("bgg_id")
        if bid is None:
            continue
        s = str(bid)
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def load_existing_prices(out_path: Path) -> dict[str, Any] | None:
    if not out_path.is_file():
        return None
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_cache_fresh(existing: dict[str, Any] | None, max_age_hours: float) -> bool:
    if not existing:
        return False
    ts = parse_iso_utc(str(existing.get("fetchedAt", "")))
    if ts is None:
        return False
    age = dt.datetime.now(dt.UTC) - ts
    return age.total_seconds() < max_age_hours * 3600.0


def build_query(eids: list[str], sitename: str, currency: str, destination: str) -> str:
    params = {
        "sitename": sitename,
        "eid": ",".join(eids),
        "currency": currency,
        "destination": destination,
        "sort": "CHEAP2",
    }
    return BGP_INFO_URL + "?" + urllib.parse.urlencode(params)


def main() -> int:
    ap = argparse.ArgumentParser(description="BGP price fetcher dla geeklisty MatHandel.")
    ap.add_argument("--in", dest="in_path", type=Path, default=Path(__file__).resolve().parent / "geeklist_374834.json")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "prices.json")
    ap.add_argument("--sitename", default=DEFAULT_SITENAME)
    ap.add_argument("--currency", default=DEFAULT_CURRENCY)
    ap.add_argument("--destination", default=DEFAULT_DESTINATION)
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--batch-pause-sec", type=float, default=DEFAULT_BATCH_PAUSE_SEC)
    ap.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS, help="Pomin fetch jesli istniejacy prices.json mlodszy niz N godzin (TOS API: min 1.0).")
    ap.add_argument("--force", action="store_true", help="Ignoruj cache, zawsze odpytuj.")
    ap.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC)
    ap.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    args = ap.parse_args()

    if args.max_age_hours < 1.0 and not args.force:
        print("OSTRZEZENIE: --max-age-hours < 1.0 narusza TOS API BGP (cache min 1h).", file=sys.stderr)

    if not args.in_path.is_file():
        print(f"Brak pliku wejsciowego: {args.in_path}", file=sys.stderr)
        return 1

    existing = load_existing_prices(args.out)
    if not args.force and is_cache_fresh(existing, args.max_age_hours):
        ts = existing.get("fetchedAt") if existing else "?"
        print(f"prices.json mlodszy niz {args.max_age_hours}h (fetchedAt={ts}). Pomijam fetch. Uzyj --force aby wymusic.")
        return 0

    bgg_ids = collect_bgg_ids(args.in_path)
    if not bgg_ids:
        print("Brak bgg_id w pliku wejsciowym.", file=sys.stderr)
        return 1

    print(f"Fetch {len(bgg_ids)} unikalnych bgg_id z BGP ({args.currency}/{args.destination}, sitename={args.sitename})...")

    by_bgg: dict[str, dict[str, Any] | None] = {bid: None for bid in bgg_ids}
    failed_batches = 0
    batch_size = max(1, min(args.batch_size, 50))

    for batch_idx, batch in enumerate(chunked(bgg_ids, batch_size), start=1):
        url = build_query(batch, args.sitename, args.currency, args.destination)
        try:
            data = http_get_json(url, args.timeout_sec, args.retries, args.batch_pause_sec)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, json.JSONDecodeError) as e:
            failed_batches += 1
            print(f"  batch {batch_idx} BLAD: {e} (pomijam)", file=sys.stderr)
            time.sleep(max(0.0, args.batch_pause_sec))
            continue

        items = data.get("items", []) if isinstance(data, dict) else []
        seen_in_batch: set[str] = set()
        for item in items:
            ext_id = item.get("external_id")
            if ext_id is None:
                continue
            ext_key = str(ext_id)
            if ext_key in seen_in_batch:
                continue
            seen_in_batch.add(ext_key)

            best, total_offers, in_stock = pick_best_offer(item.get("prices") or [])
            if best is None:
                by_bgg[ext_key] = {
                    "minProduct": None,
                    "url": item.get("url"),
                    "offers": total_offers,
                    "inStock": in_stock,
                    "stock": None,
                }
                continue

            try:
                min_product = float(best.get("product"))
            except (TypeError, ValueError):
                min_product = None

            by_bgg[ext_key] = {
                "minProduct": min_product,
                "url": item.get("url") or best.get("link"),
                "offers": total_offers,
                "inStock": in_stock,
                "stock": str(best.get("stock", "")).upper() or None,
            }

        with_prices = sum(1 for v in by_bgg.values() if v and v.get("minProduct") is not None)
        print(f"  batch {batch_idx}: zwrocono {len(items)} itemow ({with_prices}/{len(bgg_ids)} z cena lacznie)")
        time.sleep(max(0.0, args.batch_pause_sec))

    payload = {
        "fetchedAt": now_utc_iso(),
        "currency": args.currency,
        "destination": args.destination,
        "sitename": args.sitename,
        "source": "https://boardgameprices.co.uk/api/plugin",
        "byBggId": by_bgg,
    }

    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    n_with = sum(1 for v in by_bgg.values() if v and v.get("minProduct") is not None)
    print(f"Zapisano {args.out} ({n_with}/{len(bgg_ids)} gier z cena, {failed_batches} bledow batcha).")
    return 0 if failed_batches == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
