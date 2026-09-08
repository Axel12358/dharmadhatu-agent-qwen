#!/usr/bin/env python3
"""
Scraper de Psychill Space (psychill.space/events) — calendario global psychill/psytrance.

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
except: _extractor=None
LIST_URL="https://psychill.space/events"
HEADERS={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
MAX_EVENTOS=40
MESES={"jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06","jul":"07","aug":"08","sep":"09","oct":"10","nov":"11","dec":"12"}
def _norm_fecha(t):
    m=re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",t)
    if m: return f"{m.group(3)}-{MESES.get(m.group(2).lower()[:3],'01')}-{m.group(1).zfill(2)}"
    m=re.search(r"(\d{4})-(\d{2})-(\d{2})",t)
    if m: return m.group(0)
    return "N/A"
def _sesion():
    s=requests.Session()
    # Direct sin Tor (Cloudflare bloquea exits)
    s.headers.update(HEADERS)
    return s
def scrape_psychill_space(max_eventos=MAX_EVENTOS)->List[Dict]:
    print("🎵 Scraping Psychill Space (events)...")
    s=_sesion()
    try:
        r=s.get(LIST_URL, timeout=25); r.raise_for_status()
        soup=BeautifulSoup(r.text,"lxml")
    except Exception as e:
        print(f"  ❌ Psychill Space: {e}"); return []
    eventos=[]; vistos=set()
    for tag in soup.find_all(["h2","h3","h4","a"]):
        t=tag.get_text(" ", strip=True)
        if len(t)<6 or len(t)>100: continue
        # Filtrar por keywords evento
        if not any(k in t.lower() for k in ["fest","gathering","festival","event","psy","chill"]): continue
        if t.lower() in vistos: continue
        parent=tag.parent.get_text(" ", strip=True) if tag.parent else t
        if "2026" not in parent and "2027" not in parent: continue
        fecha=_norm_fecha(parent)
        pais="N/A"; lugar="N/A"
        for p in ["Portugal","Spain","Germany","France","Greece","Poland","Hungary","Croatia","Italy","Australia","Canada","USA","Brazil","India","Japan","Mexico","Israel","Switzerland","Netherlands","UK"]:
            if p.lower() in parent.lower(): pais=p; lugar=p; break
        link=tag.get("href") if tag.name=="a" else ""
        if link and not link.startswith("http"): link="https://psychill.space"+link
        if not link: link=LIST_URL
        sub="psychill"
        if _extractor:
            try: sub=_extractor.clasificar_subgenero(f"{t} psychill") or "psychill"
            except: pass
        vistos.add(t.lower())
        eventos.append({"nombre": t[:120],"fecha": fecha,"lugar": lugar,"pais": pais,"fuente": "Psychill Space","organizador": t.split()[0],"email": link,"link": link,"subgenero": sub,"descripcion": parent[:400]})
        if len(eventos)>=max_eventos: break
    print(f"  ✅ Psychill Space: {len(eventos)} eventos")
    return eventos[:max_eventos]
if __name__=="__main__":
    for ev in scrape_psychill_space()[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['pais']}")
