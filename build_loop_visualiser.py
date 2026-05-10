#!/usr/bin/env python3
"""
Składa LoopVisualiser.html z:
  - Wyniki.txt (TradeMaximizer)
  - Lista skrócona (HTML z polskimathandel.github.io — numery pozycji = ID w wynikach)
  - geeklist_374834.json (lista_index, link do itemu, body_text, image_url / miniatury)

Uruchomienie: python3 build_loop_visualiser.py
Opcje: --wyniki, --lista-url, --lista-file, --geeklist-json, --out
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path

DEFAULT_LISTA_URL = (
    "https://raw.githubusercontent.com/PolskiMatHandel/PolskiMatHandel.github.io/"
    "master/Polski%20MatHandel%20%2359/Lista%20skrocona.html"
)

TRADE_LINE = re.compile(
    r"^\(([^)]+)\)\s+(\S+)\s+receives\s+\(([^)]+)\)\s+(\S+)\s*$"
)
GROUP_SIZES_RE = re.compile(r"Group sizes\s*=\s*([\d ]+)")
NUM_TRADES_RE = re.compile(r"TRADE LOOPS\s*\((\d+)\s+total trades\)")


def fetch_or_read_lista(url: str, cache: Path) -> str:
    if cache.is_file():
        return cache.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": "LoopVisualiser-build/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read().decode("utf-8", errors="replace")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(data, encoding="utf-8")
    return data


def parse_lista_skrocona(html: str) -> dict[int, list[int]]:
    """Numer pozycji z listy skróconej -> lista bgg_id (pierwszy = główna okładka)."""
    out: dict[int, list[int]] = {}
    for block in re.findall(r"<li>(.*?)</li>", html, re.DOTALL):
        m = re.search(r">(\d+)\.</a>", block)
        if not m:
            continue
        tid = int(m.group(1))
        ids = [int(x) for x in re.findall(r"/(?:boardgame|boardgameexpansion)/(\d+)", block)]
        if ids:
            out[tid] = ids
    return out


def parse_wyniki(text: str) -> tuple[list[tuple[str, str, str, str]], list[int], int]:
    """Zwraca (krawędzie jako raw tuple), rozmiary grup, liczbę wymian."""
    if "ITEM SUMMARY" not in text:
        raise ValueError("Brak sekcji ITEM SUMMARY — niepoprawny plik wyników.")
    head, _ = text.split("ITEM SUMMARY", 1)
    edges: list[tuple[str, str, str, str]] = []
    for line in head.splitlines():
        line = line.strip()
        m = TRADE_LINE.match(line)
        if not m:
            continue
        ru, ri, su, si = m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)
        edges.append((ru, ri, su, si))

    m_sz = GROUP_SIZES_RE.search(text)
    if not m_sz:
        raise ValueError("Nie znaleziono wiersza Group sizes.")
    sizes = [int(x) for x in m_sz.group(1).split() if x.strip()]

    m_nt = NUM_TRADES_RE.search(text)
    n_trades = int(m_nt.group(1)) if m_nt else len(edges)

    if sum(sizes) != len(edges):
        raise ValueError(
            f"Suma grup ({sum(sizes)}) != liczba krawędzi ({len(edges)}). Sprawdź plik."
        )
    return edges, sizes, n_trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wyniki", type=Path, default=Path(__file__).resolve().parent / "Wyniki.txt")
    ap.add_argument("--lista-url", default=DEFAULT_LISTA_URL)
    ap.add_argument(
        "--lista-file",
        type=Path,
        default=Path(__file__).resolve().parent / "Lista-skrocona-59.html",
        help="Cache listy skróconej (pobierany z --lista-url jeśli brak)",
    )
    ap.add_argument(
        "--geeklist-json",
        type=Path,
        default=Path(__file__).resolve().parent / "geeklist_374834.json",
    )
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "LoopVisualiser.html")
    args = ap.parse_args()

    if not args.wyniki.is_file():
        raise SystemExit(f"Brak pliku wyników: {args.wyniki}")

    wyniki_text = args.wyniki.read_text(encoding="utf-8", errors="replace")
    edges, group_sizes, total_trades = parse_wyniki(wyniki_text)

    lista_html = fetch_or_read_lista(args.lista_url, args.lista_file)
    lista_map = parse_lista_skrocona(lista_html)

    by_lista: dict[int, dict] = {}
    by_bgg: dict[str, dict] = {}
    raw_games = json.loads(args.geeklist_json.read_text(encoding="utf-8"))
    for row in raw_games:
        li = row.get("lista_index")
        if li is not None:
            by_lista[int(li)] = row
        bid = row.get("bgg_id")
        if bid is not None and str(bid) not in by_bgg:
            by_bgg[str(bid)] = row

    def item_payload(item_id: int) -> dict:
        ids = lista_map.get(item_id, [])
        primary = ids[0] if ids else None
        gl = by_lista.get(item_id)

        title = None
        thumb = None
        image = None
        bgg_url = None
        geeklist_item_url: str | None = None
        description = ""
        bgg_id_out = primary

        if gl:
            title = gl.get("name")
            thumb = gl.get("thumbnail_url")
            image = gl.get("image_url") or thumb
            bgg_url = gl.get("bgg_game_url")
            geeklist_item_url = gl.get("geeklist_item_url")
            description = (gl.get("body_text") or "").strip()
            if gl.get("bgg_id") is not None:
                bgg_id_out = gl["bgg_id"]

        row_bgg = by_bgg.get(str(primary)) if primary is not None else None
        if row_bgg:
            if not title:
                title = row_bgg.get("name")
            if not thumb:
                thumb = row_bgg.get("thumbnail_url")
            if not image:
                image = row_bgg.get("image_url") or thumb
            if not bgg_url:
                bgg_url = row_bgg.get("bgg_game_url")
            if not geeklist_item_url:
                geeklist_item_url = row_bgg.get("geeklist_item_url")
            if not description and row_bgg.get("body_text"):
                description = (row_bgg.get("body_text") or "").strip()

        if not title and primary is not None:
            title = f"BGG {primary}"
        elif not title:
            title = f"Pozycja #{item_id}"

        if primary is not None and not bgg_url:
            bgg_url = f"https://boardgamegeek.com/boardgame/{primary}"

        if not image:
            image = thumb

        click_url = geeklist_item_url or bgg_url

        meta = f"#{item_id}"
        if primary:
            meta += f" · BGG {primary}"
        if len(ids) > 1:
            meta += f" (+{len(ids) - 1})"

        return {
            "itemId": item_id,
            "bggId": bgg_id_out,
            "title": title,
            "thumb": thumb,
            "image": image,
            "bggUrl": bgg_url,
            "geeklistItemUrl": click_url,
            "description": description,
            "meta": meta,
        }

    loops: list[list[dict]] = []
    off = 0
    for gsz in group_sizes:
        chunk = edges[off : off + gsz]
        off += gsz
        chain: list[dict] = []
        for ru, ri, su, si in chunk:
            i_sent = int(si)
            chain.append(
                {
                    "sender": su,
                    "receiver": ru,
                    "itemSent": i_sent,
                    "game": item_payload(i_sent),
                }
            )
        loops.append(chain)

    payload = {
        "meta": {
            "edition": 59,
            "geeklistId": 374834,
            "totalTrades": total_trades,
            "numLoops": len(loops),
            "groupSizes": group_sizes,
            "wynikiSource": "https://polskimathandel.github.io/Polski%20MatHandel%20%2359/Wyniki/Koncowe/Wyniki.txt",
        },
        "loops": loops,
    }

    template_path = Path(__file__).resolve().parent / "LoopVisualiser.template.html"
    if not template_path.is_file():
        raise SystemExit(f"Brak szablonu {template_path}")
    template = template_path.read_text(encoding="utf-8")
    json_str = json.dumps(payload, ensure_ascii=False)
    if "</script>" in json_str.lower():
        raise SystemExit("Dane zawierają niebezpieczny ciąg — przerwano.")
    marker = "__LOOP_DATA_JSON__"
    if marker not in template:
        raise SystemExit(f"Brak znacznika {marker} w szablonie.")
    out_html = template.replace(marker, json_str)
    args.out.write_text(out_html, encoding="utf-8")
    print(f"Zapisano {args.out} ({len(loops)} pętli, {total_trades} wymian).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
