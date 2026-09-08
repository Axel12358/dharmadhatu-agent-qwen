"""Fase 2 — Monitorización proactiva de ediciones 2027.

A partir de la watchlist de predicciones (eventos_predichos_2027.csv), vigila
los festivales/organizadores más confiables y busca si YA publicaron su evento
de 2027. Esto nos adelanta a los anuncios oficiales: en cuanto el organizador
crea la página de Facebook del evento 2027, la detectamos y la confirmamos.

Reusa el buscador multi-motor (Fase 3) para esquivar rate-limits de DDG.
"""
from __future__ import annotations

import csv
import random
import re
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PRED_PATH = PROJECT_ROOT / "eventos_predichos_2027.csv"
OUT_PATH = PROJECT_ROOT / "eventos_confirmados_2027.csv"

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
          "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
          "noviembre": 11, "diciembre": 12}


def _buscar():
    from scrapers.facebook_dorks import _buscar_dork_multimotor_tor
    return _buscar_dork_multimotor_tor


def _extracto():
    from scrapers.event_extractor import EventExtractor
    return EventExtractor()


def _fecha_2027(blob: str):
    """Extrae una fecha con 2027 del texto del snippet, si existe."""
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", blob)
    if m and m.group(1) == "2027":
        return m.group(0)
    # formato "25-27 julio 2027" / "july 2027"
    m = re.search(r"(\d{1,2})[-.](\d{1,2})?\s*([a-z]+)\s+2027", blob, re.I)
    if m:
        mes = _MESES.get(m.group(3).lower())
        if mes:
            return f"2027-{mes:02d}"
    m = re.search(r"([a-z]+)\s+2027", blob, re.I)
    if m and m.group(1).lower() in _MESES:
        return f"2027-{_MESES[m.group(1).lower()]:02d}"
    return ""


def monitor_2027(max_candidatos: int = 25, timeout: int = 15) -> list[dict]:
    if not PRED_PATH.exists():
        print("No existe eventos_predichos_2027.csv — corre primero la Fase 1")
        return []
    preds = list(csv.DictReader(open(PRED_PATH, encoding="utf-8")))
    preds.sort(key=lambda d: float(d.get("confianza") or 0), reverse=True)

    # Dedupe por 'base' y toma los top-N
    seen, terms = set(), []
    for p in preds:
        b = (p.get("base") or "").strip()
        if not b or b in seen:
            continue
        seen.add(b)
        terms.append(p)
        if len(terms) >= max_candidatos:
            break

    buscar = _buscar()
    ex = _extracto()
    campos = ["base", "nombre_encontrado", "link", "fecha_2027",
              "organizador_pred", "lugar_pred", "confianza_base"]

    def _buscar_segura(dork, timeout, tope=45):
        box = {}

        def _w():
            try:
                box["r"] = buscar(dork, timeout)
            except Exception:
                box["r"] = []
        t = threading.Thread(target=_w, daemon=True)
        t.start()
        t.join(tope)
        return box.get("r", [])

    # Carga lo ya confirmado (aditivo por link): los kills no pierden avance
    prev = list(csv.DictReader(open(OUT_PATH, encoding="utf-8"))) if OUT_PATH.exists() else []
    vistos = {r.get("link") for r in prev}
    confirmados: list[dict] = list(prev)

    def _guardar():
        with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=campos)
            w.writeheader()
            w.writerows(confirmados)

    for p in terms:
        base = p["base"]
        # Dork sin comillas: DDG-lite solo devuelve así; los otros motores
        # (Bing/Startpage/Mojeek) refinan con site: + el nombre.
        dork = f"site:facebook.com/events {base} 2027"
        res = _buscar_segura(dork, timeout)  # hilo daemon: nunca cuelga
        for hit in res:
            url = hit.get("url", "")
            if "facebook.com/events" not in url:
                continue
            texto = f"{hit.get('titulo', '')} {hit.get('snippet', '')}"
            if "2027" not in texto.lower():
                continue
            evs = ex.extract_all(texto[:1500], "Facebook (monitor2027)", url)
            fecha = ""
            for e in evs:
                f = e.get("fecha") or ""
                if "2027" in str(f):
                    fecha = f
                    break
            if not fecha:
                fecha = _fecha_2027(texto) or "por_extraer"
            if url in vistos:
                continue
            vistos.add(url)
            confirmados.append({
                "base": base,
                "nombre_encontrado": hit.get("titulo", "")[:120],
                "link": url,
                "fecha_2027": fecha,
                "organizador_pred": p.get("organizador", ""),
                "lugar_pred": p.get("lugar", ""),
                "confianza_base": p.get("confianza", ""),
            })
        time.sleep(random.uniform(2.0, 4.0))
        # escritura incremental: si se detiene, conservamos lo confirmado
        _guardar()

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(confirmados)
    return confirmados


if __name__ == "__main__":
    res = monitor_2027(max_candidatos=15)
    print(f"Confirmados 2027 detectados: {len(res)}")
    for c in res[:15]:
        print(f"  {c['fecha_2027']:12} {c['base'][:28]:28} {c['link'][:50]}")
