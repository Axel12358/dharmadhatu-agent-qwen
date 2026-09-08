#!/usr/bin/env python3
"""
Scraper de Psytrancefestivals.tv — agenda 2026-2027.

Fuente: https://www.psytrancefestivals.tv/
- Gratis, sin login, sin API key. Lista estática 71 festivales 2026-2027.
- Parseo BS4 de texto con regex fecha/lugar.
- Requests + get_active_proxies (Tor fallback, nunca IP real).

Requiere: requests, bs4, lxml, dateparser opcional
"""
import re
import sys
import time
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
    def get_active_proxies():
        return {}

try:
    from scrapers.event_extractor import EventExtractor
    _extractor = EventExtractor()
except Exception:
    _extractor = None

BASE_URL = "https://www.psytrancefestivals.tv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
MAX_EVENTOS = 80

# Meses para normalizar fecha
MESES = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "sept": "09",
    "oct": "10", "nov": "11", "dec": "12",
}


def _normalizar_fecha(texto: str) -> str:
    """Intenta extraer YYYY-MM-DD de texto tipo '22 to 26 January 2026' o '5 to 8 of February 2026'."""
    t = texto.lower()
    # 22 to 26 January 2026
    m = re.search(r"(\d{1,2})\s+to\s+\d{1,2}\s+(?:of\s+)?([a-z]+)\s+(\d{4})", t)
    if m:
        dia = m.group(1).zfill(2)
        mes = MESES.get(m.group(2)[:3], MESES.get(m.group(2), "01"))
        anio = m.group(3)
        return f"{anio}-{mes}-{dia}"
    # 22 January 2026
    m = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", t)
    if m:
        dia = m.group(1).zfill(2)
        mes = MESES.get(m.group(2)[:3], MESES.get(m.group(2), "01"))
        anio = m.group(3)
        return f"{anio}-{mes}-{dia}"
    # 2026-01-22
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if m:
        return m.group(0)
    return "N/A"


def _sesion():
    s = requests.Session()
    # Direct sin Tor (Cloudflare bloquea exits)
    s.headers.update(HEADERS)
    return s


def scrape_psytrancefestivals_tv(max_eventos=MAX_EVENTOS) -> List[Dict]:
    """Scrapea agenda psytrancefestivals.tv."""
    print("🎵 Scraping Psytrancefestivals.tv (agenda 2026-2027)...")
    s = _sesion()
    try:
        r = s.get(BASE_URL, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"  ❌ Psytrancefestivals.tv: {e}")
        return []

    texto = soup.get_text(" ", strip=True)
    # Patrón: "Festival Name - Location 22 to 26 January 2026"
    # El sitio lista como "Ligor Spirit Festival - Thaïland Nakhon Si Thammarat , Thaïland 22 to 26 January 2026"
    # Estrategia: buscar bloques que contienen fecha + location
    eventos = []
    # Buscar todos los textos que contienen un patrón de fecha 2026/2027
    # Usamos el HTML crudo para extraer líneas
    html = r.text
    # Extraer candidatos: buscar nombres de festival seguidos de fecha
    # Patrón simple: capturar "<festival name> ... <fecha>"
    # El sitio usa mucho texto corrido, así que buscamos por regex de fecha y tomamos contexto previo
    fechas = list(re.finditer(r"(\d{1,2}\s+to\s+\d{1,2}\s+(?:of\s+)?[A-Za-z]+\s+202[67])|(\d{1,2}\s+[A-Za-z]+\s+202[67])", texto))
    # Intentar extraer eventos por bloques: buscar en el soup los elementos que contienen festival
    candidates = []
    for tag in soup.find_all(["p", "div", "li", "h2", "h3", "span"]):
        t = tag.get_text(" ", strip=True)
        if len(t) < 20 or len(t) > 300:
            continue
        if "2026" not in t and "2027" not in t:
            continue
        if "festival" not in t.lower() and "gathering" not in t.lower():
            continue
        candidates.append(t)

    vistos = set()
    for c in candidates:
        if len(eventos) >= max_eventos:
            break
        fecha = _normalizar_fecha(c)
        # Nombre: primera parte antes de " - " o antes de fecha
        nombre = c.split(" 202")[0].strip()
        # Limpiar nombre: tomar hasta 80 chars, quitar locación si está pegada
        # Si contiene " - ", tomar antes
        if " - " in nombre:
            nombre = nombre.split(" - ")[0].strip()
        # Si sigue muy largo, cortar por coma
        if len(nombre) > 80:
            nombre = nombre[:80].rsplit(" ", 1)[0]
        if not nombre or len(nombre) < 4 or nombre.lower() in vistos:
            continue
        # Lugar: extraer después de " - " o la locación mencionada
        lugar = "N/A"
        pais = "N/A"
        # Buscar país: última palabra antes de fecha suele ser país
        # Ej: "Nakhon Si Thammarat , Thaïland 22 to 26 January 2026" -> Thaïland
        m_pais = re.search(r",\s*([A-Za-zÀ-ÿ\s]+?)\s+\d{1,2}\s+to", c)
        if m_pais:
            pais = m_pais.group(1).strip().split(",")[-1].strip()[:40]
            lugar = pais
        else:
            # Fallback: buscar país conocido
            for p in ["Thailand", "India", "Cambodia", "Panama", "Costa Rica", "Taiwan", "Mexico", "Nepal", "France", "Germany", "Spain", "Australia", "UK", "England", "Czech", "Portugal", "Romania", "Italy", "Switzerland", "Netherlands", "Canada", "Estonia", "Belgium", "Hungary", "Russia", "Finland", "Lithuania", "Croatia", "Serbia", "Greece", "Mozambique", "Slovenia", "Brazil", "Japan", "South Africa"]:
                if p.lower() in c.lower():
                    pais = p
                    lugar = p
                    break
        link = BASE_URL
        # Intentar encontrar link específico si existe
        for a in soup.find_all("a", href=True):
            if nombre.lower()[:10] in a.get_text().lower()[:20]:
                href = a["href"]
                if href.startswith("http"):
                    link = href
                elif href.startswith("/"):
                    link = BASE_URL + href
                break
        subgenero = "psytrance"
        if _extractor:
            try:
                subgenero = _extractor.clasificar_subgenero(f"{nombre} {c} psytrance")
            except Exception:
                pass
        vistos.add(nombre.lower())
        eventos.append({
            "nombre": nombre[:120],
            "fecha": fecha,
            "lugar": lugar,
            "pais": pais,
            "fuente": "Psytrancefestivals.tv",
            "organizador": nombre.split()[0] if len(nombre.split()) <= 3 else "Psytrancefestivals.tv",
            "email": link,
            "link": link,
            "subgenero": subgenero or "psytrance",
            "descripcion": c[:400],
        })

    # Fallback: si parseo por tags dio poco, intentar regex directo sobre texto
    if len(eventos) < 10:
        pattern = re.compile(r"([A-Z][A-Za-z\s\-']+(?:Festival|Gathering)[A-Za-z\s\-']*)\s*[-–]\s*([^0-9]{5,60}?)\s*(\d{1,2}\s+to\s+\d{1,2}\s+(?:of\s+)?[A-Za-z]+\s+202[67])", re.I)
        for m in pattern.finditer(texto):
            if len(eventos) >= max_eventos:
                break
            nombre = m.group(1).strip()[:80]
            lugar_raw = m.group(2).strip()[:40]
            fecha_raw = m.group(3).strip()
            if nombre.lower() in vistos:
                continue
            fecha = _normalizar_fecha(fecha_raw)
            vistos.add(nombre.lower())
            eventos.append({
                "nombre": nombre,
                "fecha": fecha,
                "lugar": lugar_raw,
                "pais": lugar_raw.split(",")[-1].strip()[:30] or "N/A",
                "fuente": "Psytrancefestivals.tv",
                "organizador": "Psytrancefestivals.tv",
                "email": BASE_URL,
                "link": BASE_URL,
                "subgenero": "psytrance",
                "descripcion": m.group(0)[:400],
            })

    print(f"  ✅ Psytrancefestivals.tv: {len(eventos)} eventos")
    return eventos[:max_eventos]


if __name__ == "__main__":
    evs = scrape_psytrancefestivals_tv()
    for ev in evs[:10]:
        print(f"  - {ev['nombre'][:50]} | {ev['fecha']} | {ev['lugar']} | {ev['pais']}")
