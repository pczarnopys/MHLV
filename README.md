# MHLV — MatHandel Loop Visualiser

Statyczna wizualizacja łańcuchów wymian z polskiego MatHandla (BGG geeklist + wyniki TradeMaximizer).
Build = pojedynczy plik `LoopVisualiser.html`, hostowany na GitHub Pages:
<https://pczarnopys.github.io/MHLV/LoopVisualiser.html>.

## Pipeline

1. **`fetch_geeklist_games.py`** — pobiera z BGG XML API geeklistę (tytuły, okładki, opisy).
   Wymaga `bgg_config.json` z `api_token` (patrz `bgg_config.example.json`).
   ```bash
   python3 fetch_geeklist_games.py --out geeklist_374834.json
   ```

2. **`fetch_bgg_prices.py`** — dla każdego unikalnego `bgg_id` z geeklisty pobiera najtańszą
   ofertę z [BoardGamePrices.co.uk](https://boardgameprices.co.uk/api/plugin)
   (waluta PLN, dostawa do PL, cena bez kosztów wysyłki, preferencja oferty `in stock`).
   Cache zgodny z TOS API (min. 1 h; default 12 h):
   ```bash
   python3 fetch_bgg_prices.py            # użyje cache jeśli prices.json < 12h
   python3 fetch_bgg_prices.py --force    # wymuś świeży fetch
   ```
   Wynik: `prices.json` (commitowany do repo, wczytywany przez build).

3. **`build_loop_visualiser.py`** — łączy `Wyniki.txt` (TradeMaximizer) +
   `Lista-skrocona-59.html` + `geeklist_374834.json` + `prices.json` przez szablon
   `LoopVisualiser.template.html` w finalny `LoopVisualiser.html`.
   ```bash
   python3 build_loop_visualiser.py
   ```

## Wyświetlanie cen

Każdy kafelek gry pokazuje pigułkę "od X,XX zł" pod wierszem odbiorcy. Klik otwiera listę
ofert na BoardGamePrices.co.uk. Brak danych: "— brak ofert PL". Stopka zawiera linkback
i datę ostatniego fetcha (wymagane przez TOS API BGP).
