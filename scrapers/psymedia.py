#!/usr/bin/env python3
"""
Scraper de Psymedia (psymedia.co.za/calendar) — calendario psytrance 2026.

Gratis, sin login, HTML estático. Tor fallback.
"""
import re
import sys
from pathlib import Path
from typing import List, Dict
import requests
from bs4 import BeautifulSoup

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
try:
    from core.http_client import get_active_proxies
except Exception:
    def get_active_proxies(): return {}
try:
    from scrapers.event_extractor import EventExtractor
    _extractor = EventExtractor()
except Exception:
    _extractor = None

BASE_URL = "https://psymedia.co.za"
LIST_URL = f"{BASE_URL}/calendar/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
MAX_EVENTOS = 50
MESES = {"january":"01","february":"02","march":"03","april":"04","may":"05","june":"06","july":"07","august":"08","september":"09","october":"10","november":"11","december":"12"}

def _norm_fecha(t: str) -> str:
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", t)
    if m:
        return f"{m.group(3)}-{MESES.get(m.group(2).lower()[:3], '01')}-{m.group(1).zfill(2)}"
    return "N/A"

def _sesion():
    s = requests.Session()
    # Direct sin Tor (Cloudflare bloquea exits); FB/IG siguen por Tor
    s.headers.update(HEADERS)
    return s

def scrape_psymedia(max_eventos=MAX_EVENTOS) -> List[Dict]:
    print("🎵 Scraping Psymedia (calendar 2026)...")
    s = _sesion()
    try:
        r = s.get(LIST_URL, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  ❌ Psymedia: {e}")
        return []
    eventos = []
    vistos = set()
    for tag in soup.find_all(["h2","h3","h4","a"]):
        t = tag.get_text(" ", strip=True)
        if len(t) < 8 or len(t) > 120: continue
        if "festival" not in t.lower() and "gathering" not in t.lower(): continue
        if t.lower() in vistos: continue
        # Buscar fecha cerca
        parent = tag.parent.get_text(" ", strip=True) if tag.parent else t
        fecha = _norm_fecha(parent)
        if fecha == "N/A":
            # buscar en siguiente sibling
            nxt = tag.find_next_sibling()
            if nxt: fecha = _norm_fecha(nxt.get_text(" ", strip=True))
        link = tag.get("href") if tag.name == "a" else ""
        if link and not link.startswith("http"): link = BASE_URL + link
        if not link: link = LIST_URL
        pais = "N/A"
        for p in ["South Africa","Germany","Portugal","Spain","Hungary","Czech","France","UK","Australia","Brazil","India","Netherlands","Switzerland","Croatia","Serbia","Greece","Romania","Italy"]:
            if p.lower() in parent.lower(): pais = p; break
        sub = "psytrance"
        if _extractor:
            try: sub = _extractor.clasificar_subgenero(f"{t} psytrance") or "psytrance"
            except: pass
        vistos.add(t.lower())
        eventos.append({"nombre": t[:120],"fecha": fecha,"lugar": pais,"pais": pais,"fuente": "Psymedia","organizador": t.split()[0],"email": link,"link": link,"subgenero": sub,"descripcion": parent[:400]})
        if len(eventos) >= max_eventos: break
    print(f"  ✅ Psymedia: {len(eventos)} eventos")
    return eventos[:max_eventos]

if __name__ == "__main__":
    for ev in scrape_psymedia()[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['pais']}")
