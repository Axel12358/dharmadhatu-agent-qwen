# Dharmadhatu Agent - Psytrance Event OSINT Pipeline

Automated pipeline for discovering and enriching psytrance/psychedelic trance events worldwide. All traffic routed through Tor for privacy. Uses DDG/SearXNG for SERP mining, Telegram channel crawling, and multi-source triangulation.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    LaunchAgents (persistent)              │
│  com.dharma.loop (enrichment)  │  com.dharma.orquestador│
│  com.dharma.enricher (venue)   │                        │
└──────────────┬──────────────────┬───────────────────────┘
               │                  │
    ┌──────────▼──────────┐  ┌───▼──────────────┐
    │  loop_completar.py  │  │  core/orquestador │
    │  ──────────────────│  │  ────────────────│
    │  1. RA email batch  │  │  2. Org tail mining│
    │  2b. SERP city sweep│  │  3. MCP classify   │
    │  2c. Telegram crawl │  │  4. Venue enrich   │
    │  3. Rama A email    │  │                    │
    │  25. rellenar_na    │  │                    │
    └──────────┬──────────┘  └───┬──────────────┘
               │                  │
    ┌──────────▼──────────────────▼──────────────┐
    │              eventos_encontrados.csv         │
    │  (fcntl.flock cooperative lock)              │
    └─────────────────────────────────────────────┘
```

## Pipeline Stages

### Rama B: Organizer Discovery
1. **SERP city sweep** (`minar_organizadores_fb_serp`): DDG `site:facebook.com/events <city> <subgenre> <year>` → snippet "Event in X by ORGANIZER on <day>" → match URL exacta
2. **Telegram** (`minar_organizadores_telegram`): DDG `site:t.me <city> psy trance events` → crawl `t.me/s/<channel>` → extract FB event URLs + organizer from message text/author
3. **Rama A** (`minar_email_organizador`): For known organizers → DDG `"<org>" <city> email contact` → web scrape → email regex

### Rama A: Organizer Enrichment
- **Email cascade**: Organizer → website → email regex → SMTP verify
- **Venue-first**: venue_cache.json → apply to all events of same venue
- **Local fill**: subgenre keywords, city→country lookup, continent derivation

## Data Quality

| Field | Coverage | Method |
|-------|----------|--------|
| Subgenre | 100% | Keyword classifier |
| Country | 99.5% | City→country seed + learned |
| Continent | 92.5% | Country→continent derivation |
| Organizer | 76% | SERP + slug-derived + RA |
| Email | 20% | RA venue-first + Rama A |

### Organizer Types
- `promoter`: Extracted via SERP/RA/Goabase/Telegram (high quality)
- `venue`: Derived from URL slug (facebook.com/events/<venue-slug>)
- Empty: Unknown / not yet discovered

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure Tor (macOS)
brew install tor
brew services start tor

# Set up LaunchAgents
cp ~/Library/LaunchAgents/com.dharma.*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dharma.loop.plist
```

## Git Workflow

- `main`: Production-ready code
- `feature/*`: New features (current: `feature/mcp-refactor`)
- `experiment/*`: Research branches

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Key Files

| File | Purpose |
|------|---------|
| `loop_completar.py` | Main enrichment loop |
| `core/csv_lock.py` | Cooperative file locking |
| `core/rellenar_na.py` | Zero-cost local fills |
| `core/serp_tor.py` | DDG/SearXNG via Tor |
| `venue_enricher.py` | Venue contact cache |
| `.opencode/skills/fb-organizer-osint/SKILL.md` | Reusable OSINT skill |

## Constraints

- **Tor-only**: All HTTP traffic via `socks5h://127.0.0.1:9050`
- **No real IP**: Never expose personal IP or accounts
- **No paid APIs**: Apify, Meta Content Library excluded
- **CSV lock mandatory**: All writers must use `csv_locked_rows()`

## License

Private - Internal use only
