#!/usr/bin/env python3
"""
Scraper de Setline (setlineapp.com/festivals/genre/psytrance) — 20 festivales 2026-2027.

Gratis, sin login. Tor fallback.
"""
import re, sys
from pathlib import Path
from typing import List, Dict
import requests
from bs4 import BeautifulSoup
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path: sys.path.insert(0, _PROJECT_ROOT)
try:
    from core.http_client import get_active_proxies
except: 
    def get_active_proxies(): return {}
try:
    from scrapers.event_extractor import EventExtractor
    _extractor = EventExtractor()
except: _extractor = None
LIST_URL = "https://setlineapp.com/festivals/genre/psytrance"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
MAX_EVENTOS = 30
MESES = {"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06","jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12","january":"01","february":"02","march":"03","april":"04","june":"06","july":"07","august":"08","september":"09","october":"10","november":"11","december":"12"}
def _norm_fecha(t):
    m=re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})",t)
    if m: return f"{m.group(3)}-{MESES.get(m.group(1).lower()[:3],'01')}-{m.group(2).zfill(2)}"
    m=re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",t)
    if m: return f"{m.group(3)}-{MESES.get(m.group(2).lower()[:3],'01')}-{m.group(1).zfill(2)}"
    return "N/A"
def _sesion():
    s=requests.Session()
    # Direct sin Tor (Cloudflare bloquea exits)
    s.headers.update(HEADERS)
    return s
def scrape_setline(max_eventos=MAX_EVENTOS)->List[Dict]:
    print("🎵 Scraping Setline (psytrance 2026-2027)...")
    s=_sesion()
    try:
        r=s.get(LIST_URL, timeout=25); r.raise_for_status()
        soup=BeautifulSoup(r.text,"lxml")
    except Exception as e:
        print(f"  ❌ Setline: {e}"); return []
    eventos=[]; vistos=set()
    for card in soup.find_all(["div","article","li"], limit=100):
        t=card.get_text(" ", strip=True)
        if len(t)<20 or len(t)>600: continue
        if "2026" not in t and "2027" not in t: continue
        # Buscar título
        title_el=card.find(["h2","h3","a"])
        nombre=title_el.get_text(strip=True) if title_el else ""
        if len(nombre)<4 or len(nombre)>80: continue
        if nombre.lower() in vistos: continue
        fecha=_norm_fecha(t)
        # Lugar: buscar ciudad/país
        lugar="N/A"; pais="N/A"
        for p in ["Australia","New Zealand","Portugal","Germany","Brazil","USA","United States","Canada","UK","Spain","Hungary","Croatia","Serbia","Greece","France","Netherlands","Switzerland","Japan","Mexico","Israel"]:
            if p.lower() in t.lower(): pais=p; lugar=p; break
        link=LIST_URL
        a=card.find("a", href=True)
        if a:
            href=a["href"]
            if href.startswith("http"): link=href
            elif href.startswith("/"): link="https://setlineapp.com"+href
        sub="psytrance"
        if _extractor:
            try: sub=_extractor.clasificar_subgenero(f"{nombre} psytrance") or "psytrance"
            except: pass
        vistos.add(nombre.lower())
        eventos.append({"nombre": nombre[:120],"fecha": fecha,"lugar": lugar,"pais": pais,"fuente": "Setline","organizador": nombre.split()[0],"email": link,"link": link,"subgenero": sub,"descripcion": t[:400]})
        if len(eventos)>=max_eventos: break
    print(f"  ✅ Setline: {len(eventos)} eventos")
    return eventos[:max_eventos]
if __name__=="__main__":
    for ev in scrape_setline()[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['pais']}")
