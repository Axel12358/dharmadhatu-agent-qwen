"""Descubrimiento 2026/2027 desde Goabase.net vía su API JSON-LD, por Tor.

- Gratis y local: no requiere proxy de pago ni usa la IP real del usuario.
- Usa el proxy activo (residencial de terceros si está configurado, si no Tor).
- Guarda incrementalmente para no perder avance si se corta la red.
"""
from __future__ import annotations

import json
import csv
import time
from pathlib import Path

import requests

from core.http_client import get_active_proxies

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SALIDA = PROJECT_ROOT / "eventos_goabase_2027.csv"
LISTA_URL = "https://www.goabase.net/api/party/jsonld/?year={anio}"
# Expansión 27-08-2026: además de ?year, consultar eventtype festival/openair
# y búsqueda psytrance para capturar eventos sin año explícito.
LISTA_URLS_EXTRA = [
    "https://www.goabase.net/api/party/jsonld/?eventtype=festival",
    "https://www.goabase.net/api/party/jsonld/?eventtype=openair",
    "https://www.goabase.net/api/party/jsonld/?search=psytrance",
]
DETALLE_URL = "https://www.goabase.net/api/party/jsonld/{eid}"
CAMPOS = ["nombre", "fecha", "lugar", "pais", "organizador",
          "email", "link", "fuente", "descripcion"]
ANIOS = [2027, 2026]
TOPE_SEG = 240  # tope de reloj de pared por corrida


def _sesion():
    s = requests.Session()
    s.proxies.update(get_active_proxies())
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; dharma/1.0)"})
    return s


def _detalle(s, eid):
    try:
        r = s.get(DETALLE_URL.format(eid=eid), timeout=20)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _normalizar(d):
    if not d:
        return None
    fecha_raw = d.get("startDate", "") or ""
    fecha = fecha_raw.split("T")[0] if "T" in fecha_raw else (fecha_raw or "N/A")
    if fecha == "N/A" or not fecha.startswith(("2026", "2027")):
        return None
    loc = d.get("location", {}) or {}
    addr = loc.get("address", {}) or {}
    org = d.get("organizer", {}) or {}
    email = org.get("email") or "N/A"
    link = d.get("url") or "N/A"
    return {
        "nombre": d.get("name", "N/A"),
        "fecha": fecha,
        "lugar": loc.get("name", "N/A"),
        "pais": addr.get("addressCountry", "N/A"),
        "organizador": org.get("name", "N/A"),
        "email": email if email else "N/A",
        "link": link,
        "fuente": "Goabase",
        "descripcion": (d.get("description") or "")[:500],
    }


def escanear():
    s = _sesion()
    urls = []
    for anio in ANIOS:
        try:
            h = s.get(LISTA_URL.format(anio=anio), timeout=30).text
            d = json.loads(h)
        except Exception as e:
            print(f"  [goabase] lista {anio}: error {e}")
            continue
        for it in d.get("itemListElement", []):
            u = it.get("url")
            if u:
                urls.append(u)
    # Fuentes extra: festival/openair/psytrance (aditivo, dedup después)
    for url_extra in LISTA_URLS_EXTRA:
        try:
            h = s.get(url_extra, timeout=30).text
            d = json.loads(h)
        except Exception as e:
            print(f"  [goabase] lista extra {url_extra}: error {e}")
            continue
        for it in d.get("itemListElement", []):
            u = it.get("url")
            if u:
                urls.append(u)
    urls = list(dict.fromkeys(urls))
    print(f"  [goabase] {len(urls)} eventos a procesar")

    fila_existentes = set()
    if SALIDA.exists():
        with SALIDA.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("link"):
                    fila_existentes.add(row["link"])

    nuevos = []
    t0 = time.time()
    for i, u in enumerate(urls, 1):
        if time.time() - t0 > TOPE_SEG:
            print("  [goabase] tope de tiempo alcanzado")
            break
        eid = u.rstrip("/").split("/")[-1]
        ev = _normalizar(_detalle(s, eid))
        if ev and ev["link"] not in fila_existentes:
            nuevos.append(ev)
            fila_existentes.add(ev["link"])
        if i % 25 == 0:
            print(f"  [goabase] {i}/{len(urls)} procesados, {len(nuevos)} nuevos")
        time.sleep(0.3)
    if nuevos:
        escribir = not SALIDA.exists()
        with SALIDA.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            if escribir:
                w.writeheader()
            w.writerows(nuevos)
    print(f"  [goabase] nuevos 2026/2027: {len(nuevos)} -> {SALIDA}")
    return nuevos


if __name__ == "__main__":
    escanear()
