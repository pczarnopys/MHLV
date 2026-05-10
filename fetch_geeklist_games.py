#!/usr/bin/env python3
"""
Pobiera geeklistę BGG (XML API): każdy wpis w kolejności z lista_index, link do itemu,
opis (body → tekst), metadane gry i okładki (pełny obraz + miniatura z /xmlapi/boardgame/).

Wymaga tokena Bearer: https://boardgamegeek.com/using_the_xml_api
Konfiguracja: bgg_config.json — patrz bgg_config.example.json
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BGG_BASE = "https://boardgamegeek.com"
GEEKLIST_PATH = "/xmlapi/geeklist/{geeklist_id}"
BOARDGAME_PATH = "/xmlapi/boardgame/{ids}"

DEFAULT_POLL_SEC = 5.0
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_PAUSE_SEC = 5.0


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def bgg_get(url: str, token: str, timeout: float = 60.0) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/xml, text/xml, */*",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return e.code, body


def fetch_xml_until_ready(url: str, token: str, poll_sec: float) -> bytes:
    deadline = time.monotonic() + 300.0
    while True:
        code, data = bgg_get(url, token)
        if code == 200:
            return data
        if code == 202:
            if time.monotonic() > deadline:
                raise RuntimeError("Timeout: BGG zwraca 202 (kolejka) zbyt długo.")
            time.sleep(poll_sec)
            continue
        if code in (429, 502, 503, 504):
            time.sleep(poll_sec)
            continue
        raise RuntimeError(f"BGG HTTP {code}: {data[:500]!r}")


def html_body_to_text(raw: str | None) -> str:
    """HTML z pola body geeklisty → zwykły tekst (bez tagów, bezpieczny do wyświetlania jako text)."""
    if not raw or not raw.strip():
        return ""
    s = html_module.unescape(raw)
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", "", s)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", "", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>\s*", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def parse_geeklist_items_ordered(xml_bytes: bytes, geeklist_id: int) -> list[dict[str, Any]]:
    """Jeden wiersz na każdy <item> w kolejności XML (lista_index = 1..N)."""
    root = ET.fromstring(xml_bytes)
    items = root.findall("./item")
    if not items:
        items = root.findall(".//item")
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        item_id_attr = item.get("id")
        if not item_id_attr:
            continue
        try:
            gid = int(item_id_attr)
        except ValueError:
            continue
        item_url = f"https://boardgamegeek.com/geeklist/{geeklist_id}/item/{gid}#item{gid}"
        body_el = item.find("body")
        body_raw = "".join(body_el.itertext()) if body_el is not None else ""
        body_text = html_body_to_text(body_raw) if body_raw else ""

        objecttype = (item.get("objecttype") or "").lower()
        subtype = (item.get("subtype") or "").lower()
        oid = item.get("objectid")
        entry_name = (item.get("objectname") or "").strip()
        if not entry_name:
            name_el = item.find("name")
            entry_name = (name_el.text or "").strip() if name_el is not None else ""

        bgg_id: int | None = None
        bgg_game_url: str | None = None
        is_game = objecttype == "thing" and bool(oid) and subtype in {"boardgame", "boardgameexpansion"}
        if is_game and oid:
            bgg_id = int(oid)
            path = "boardgameexpansion" if subtype == "boardgameexpansion" else "boardgame"
            bgg_game_url = f"https://boardgamegeek.com/{path}/{oid}"

        rows.append(
            {
                "lista_index": idx,
                "geeklist_item_id": gid,
                "geeklist_item_url": item_url,
                "body_text": body_text,
                "objecttype": item.get("objecttype") or "",
                "subtype": subtype,
                "bgg_id": bgg_id,
                "name": entry_name or None,
                "thumbnail_url": None,
                "image_url": None,
                "bgg_game_url": bgg_game_url,
            }
        )
    return rows


def parse_boardgames_xml(xml_bytes: bytes) -> dict[str, dict[str, str | None]]:
    """objectid -> { primary_name, thumbnail, image }"""
    root = ET.fromstring(xml_bytes)
    result: dict[str, dict[str, str | None]] = {}
    for bg in root.findall("boardgame"):
        oid = bg.get("objectid")
        if not oid:
            continue
        thumb_el = bg.find("thumbnail")
        image_el = bg.find("image")
        primary = None
        for nm in bg.findall("name"):
            if nm.get("primary") == "true":
                primary = (nm.text or "").strip() or None
                break
        if primary is None:
            for nm in bg.findall("name"):
                primary = (nm.text or "").strip() or None
                if primary:
                    break
        result[oid] = {
            "primary_name": primary,
            "thumbnail": (thumb_el.text or "").strip() or None if thumb_el is not None else None,
            "image": (image_el.text or "").strip() or None if image_el is not None else None,
        }
    return result


def chunked(seq: list[str], size: int) -> list[list[str]]:
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Geeklista BGG → JSON (lista_index, item URL, opis, okładki).")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "bgg_config.json",
        help="Ścieżka do JSON z api_token (i opcjonalnie geeklist_id)",
    )
    parser.add_argument("--geeklist-id", type=int, help="Nadpisuje geeklist_id z konfiguracji")
    parser.add_argument("--poll-sec", type=float, default=DEFAULT_POLL_SEC)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Liczba ID na jedno żądanie /xmlapi/boardgame/ (maks. 20)",
    )
    parser.add_argument("--batch-pause-sec", type=float, default=DEFAULT_BATCH_PAUSE_SEC)
    parser.add_argument(
        "--out",
        type=Path,
        help="Zapisz JSON do pliku zamiast stdout",
    )
    args = parser.parse_args()

    if not args.config.is_file():
        print(
            f"Brak pliku konfiguracyjnego: {args.config}\n"
            f"Skopiuj bgg_config.example.json → bgg_config.json i uzupełnij api_token.",
            file=sys.stderr,
        )
        return 1

    cfg = load_config(args.config)
    token = cfg.get("api_token")
    if not token or token.startswith("WSTAW_"):
        print("Uzupełnij poprawny 'api_token' w pliku konfiguracyjnym.", file=sys.stderr)
        return 1

    geeklist_id = int(args.geeklist_id or cfg.get("geeklist_id") or 374834)
    list_url = f"{BGG_BASE}{GEEKLIST_PATH.format(geeklist_id=geeklist_id)}"

    raw_list = fetch_xml_until_ready(list_url, token, args.poll_sec)
    rows = parse_geeklist_items_ordered(raw_list, geeklist_id)

    unique_ids: list[str] = []
    seen: set[str] = set()
    for r in rows:
        bid = r.get("bgg_id")
        if bid is None:
            continue
        s = str(bid)
        if s not in seen:
            seen.add(s)
            unique_ids.append(s)

    details: dict[str, dict[str, str | None]] = {}
    batch_size = min(max(1, args.batch_size), 20)
    for batch in chunked(unique_ids, batch_size):
        ids_param = ",".join(batch)
        game_url = f"{BGG_BASE}{BOARDGAME_PATH.format(ids=ids_param)}"
        raw_games = fetch_xml_until_ready(game_url, token, args.poll_sec)
        details.update(parse_boardgames_xml(raw_games))
        time.sleep(max(0.0, args.batch_pause_sec))

    for r in rows:
        bid = r.get("bgg_id")
        if bid is None:
            continue
        d = details.get(str(bid), {})
        r["name"] = d.get("primary_name") or r.get("name")
        r["thumbnail_url"] = d.get("thumbnail")
        r["image_url"] = d.get("image")

    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
