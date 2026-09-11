"""Descubrimiento 2026-2028 desde Goabase.net vía su API JSON-LD, por Tor.

- Gratis y local: no requiere proxy de pago ni usa la IP real del usuario.
- Usa el proxy activo (residencial de terceros si está configurado, si no Tor).
- Guarda incrementalmente para no perder avance si se corta la red.
- Fusiona los hallazgos al CSV principal bajo lock (nunca pisa el bot).
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
CSV_PRINCIPAL = PROJECT_ROOT / "eventos_encontrados.csv"
LISTA_URL = "https://www.goabase.net/api/party/jsonld/?year={anio}"
# Expansión: además de ?year, consultar eventtype festival/openair, términos
# de subgénero y status=new (eventos agregados en los últimos 7 días) para
# capturar eventos sin año explícito y la rotación semanal de novedades.
LISTA_URLS_EXTRA = [
    "https://www.goabase.net/api/party/jsonld/?eventtype=festival",
    "https://www.goabase.net/api/party/jsonld/?eventtype=openair",
    "https://www.goabase.net/api/party/jsonld/?eventtype=indoor",
    "https://www.goabase.net/api/party/jsonld/?eventtype=club",
    "https://www.goabase.net/api/party/jsonld/?search=psytrance",
    "https://www.goabase.net/api/party/jsonld/?search=goa",
    "https://www.goabase.net/api/party/jsonld/?search=darkpsy",
    "https://www.goabase.net/api/party/jsonld/?search=forest",
    "https://www.goabase.net/api/party/jsonld/?search=hitech",
    "https://www.goabase.net/api/party/jsonld/?search=fullon",
    "https://www.goabase.net/api/party/jsonld/?search=progressive",
    "https://www.goabase.net/api/party/jsonld/?search=suomisaundi",
    "https://www.goabase.net/api/party/jsonld/?search=psychill",
    "https://www.goabase.net/api/party/jsonld/?search=psybient",
    "https://www.goabase.net/api/party/jsonld/?status=new",
    "https://www.goabase.net/api/party/jsonld/?status=update",
]
DETALLE_URL = "https://www.goabase.net/api/party/jsonld/{eid}"
CAMPOS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
          "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar",
          "contactos"]
ANIOS = [2028, 2027, 2026]
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
    if fecha == "N/A" or not fecha.startswith(("2026", "2027", "2028")):
        return None
    loc = d.get("location", {}) or {}
    addr = loc.get("address", {}) or {}
    org = d.get("organizer", {}) or {}
    email = org.get("email") or "N/A"
    link = d.get("url") or "N/A"
    kw = (d.get("keywords") or []) if isinstance(d.get("keywords"), list) else []
    subgenero = ""
    if isinstance(d.get("keywords"), str):
        kw = [d["keywords"]]
    for k in kw:
        kk = (k or "").lower()
        if any(s in kk for s in ("goa", "psy", "dark", "forest", "hitech",
                                 "full", "progressive", "suomi", "chill",
                                 "bient", "ambient")):
            subgenero = kk if isinstance(k, str) else str(k)
            break
    return {
        "nombre": d.get("name", "N/A"),
        "fecha": fecha,
        "lugar": loc.get("name", "N/A"),
        "pais": addr.get("addressCountry", "N/A"),
        "continente": "", "subcontinente": "",
        "fuente": "Goabase",
        "organizador": org.get("name", "N/A"),
        "email": email if email else "N/A",
        "link": link,
        "subgenero": subgenero,
        "tipo_lugar": "",
        "contactos": "",
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
            w = csv.DictWriter(f, fieldnames=["nombre", "fecha", "lugar", "pais",
                                              "organizador", "email", "link",
                                              "fuente", "descripcion"])
            if escribir:
                w.writeheader()
            w.writerows(nuevos)
        # Fusionar al CSV principal bajo lock (aditivo por link, nunca borra)
        try:
            from core.csv_lock import csv_locked_rows
        except Exception:
            from csv_lock import csv_locked_rows
        agregados = 0
        with csv_locked_rows(CSV_PRINCIPAL, timeout=180) as (estado, _fn):
            vistos = {r.get("link") for r in estado}
            for ev in nuevos:
                if ev.get("link") and ev["link"] not in vistos:
                    estado.append({c: ev.get(c, "") for c in CAMPOS})
                    vistos.add(ev["link"])
                    agregados += 1
    print(f"  [goabase] nuevos 2026-2028: {len(nuevos)} -> CSV principal: "
          f"{agregados} agregados")
    return nuevos


if __name__ == "__main__":
    escanear()
