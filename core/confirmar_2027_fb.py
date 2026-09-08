"""Confirmación 2027 por Facebook DIRECTO (sin buscadores, sin IP real).

Parte de los links facebook.com/events que ya tenemos en el CSV (fuente
Facebook dorks / capturados). De cada página de evento extrae la página del
organizador y lee su pestaña `/events` para detectar eventos de 2027. Todo
vía curl_cffi + Tor (REGLA DURA: nunca IP real). Guarda aditivo por link.
"""
from __future__ import annotations
import csv
import re
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.enriquecer_fb_og import _get_html_safe, _rotar_safe, _run_thread  # helpers blindados
from core.tor_pool import rotar_tor

CSV_MAIN = PROJECT_ROOT / "eventos_encontrados.csv"
CSV_OUT = PROJECT_ROOT / "eventos_confirmados_2027.csv"

RESERVED = {"events", "groups", "pages", "profile.php", "gif", "photo",
            "watch", "notes", "messages", "help", "settings", "login",
            "people", "hashtag", "search", "marketplace", "gaming", "jobs",
            "fundraisers", "community", "privacy", "terms", "about",
            "friends", "notifications", "bookmarks", "saved", "reels",
            "explore", "ads", "business"}

_MESES = {"ene": 1, "enero": 1, "feb": 2, "febrero": 2, "mar": 3, "marzo": 3,
          "abr": 4, "abril": 4, "may": 5, "mayo": 5, "jun": 6, "junio": 6,
          "jul": 7, "julio": 7, "ago": 8, "agosto": 8, "sep": 9,
          "septiembre": 9, "oct": 10, "octubre": 10, "nov": 11,
          "noviembre": 11, "dic": 12, "diciembre": 12,
          "jan": 1, "apr": 4, "aug": 8, "dec": 12}

EVENT_RE = re.compile(r"https?://(?:www\.)?facebook\.com/events/(\d{8,})")
SLUG_RE = re.compile(r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9._]{3,})[/?]")
FECHA_RE = re.compile(r"(\d{1,2})\s*[-–]\s*\d{1,2}\s+([a-z]{3,})\s+2027", re.I)
FECHA2_RE = re.compile(r"([a-z]{3,})\s+2027", re.I)


def _slug_de(html: str):
    for m in SLUG_RE.finditer(html or ""):
        s = m.group(1)
        if s.lower() not in RESERVED and not s.lower().startswith("events"):
            return s
    return ""


def _fecha_2027(texto: str) -> str:
    m = FECHA_RE.search(texto or "")
    if m:
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            return f"2027-{mes:02d}"
    m = FECHA2_RE.search(texto or "")
    if m:
        mes = _MESES.get(m.group(1).lower()[:3])
        if mes:
            return f"2027-{mes:02d}"
    if "2027" in (texto or ""):
        return "por_extraer"
    return ""


def _seeds() -> list[str]:
    if not CSV_MAIN.exists():
        return []
    out = []
    for r in csv.DictReader(open(CSV_MAIN, encoding="utf-8")):
        link = (r.get("link") or "")
        if "facebook.com/events" in link:
            out.append(link.split("?")[0])
    if CSV_OUT.exists():
        for r in csv.DictReader(open(CSV_OUT, encoding="utf-8")):
            if r.get("link"):
                out.append(r["link"].split("?")[0])
    return list(dict.fromkeys(out))


def _merge(rows_new: list[dict], path: Path, campos: list[str]) -> int:
    exist = list(csv.DictReader(open(path, encoding="utf-8"))) if path.exists() else []
    vistos = {r.get("link") for r in exist}
    added = 0
    for r in rows_new:
        if r["link"] and r["link"] not in vistos:
            exist.append(r)
            vistos.add(r["link"])
            added += 1
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(exist)
    return added


def confirmar_2027_fb(max_seeds: int = 5, max_org: int = 8,
                      timeout: int = 15, rotar_cada: int = 3) -> int:
    seeds = _seeds()[:max_seeds]
    campos = ["base", "nombre_encontrado", "link", "fecha_2027",
              "organizador_pred", "lugar_pred", "confianza_base"]
    confirmados: list[dict] = []
    orgs_vistos = set()
    i = 0
    for seed in seeds:
        i += 1
        if i % rotar_cada == 0:
            _rotar_safe()
        html = _get_html_safe(seed, timeout)
        slug = _slug_de(html or "")
        if not slug or slug in orgs_vistos:
            continue
        orgs_vistos.add(slug)
        if len(orgs_vistos) > max_org:
            break
        ev_html = _get_html_safe(f"https://www.facebook.com/{slug}/events", timeout)
        if not ev_html:
            continue
        for eid in dict.fromkeys(EVENT_RE.findall(ev_html or "")):
            url = f"https://www.facebook.com/events/{eid}"
            idx = ev_html.find(f"/events/{eid}")
            ctx = ev_html[max(0, idx - 400): idx + 400]
            fecha = _fecha_2027(ctx)
            if not fecha:
                continue
            confirmados.append({
                "base": slug,
                "nombre_encontrado": "",
                "link": url,
                "fecha_2027": fecha,
                "organizador_pred": "",
                "lugar_pred": "",
                "confianza_base": "",
            })
        if confirmados:
            _merge(confirmados, CSV_OUT, campos)
            _merge([{**c, "nombre": c["nombre_encontrado"] or slug,
                     "fuente": "Facebook (confirm2027)", "fecha": c["fecha_2027"]}
                    for c in confirmados if c["link"]],
                   CSV_MAIN,
                   ["nombre", "fecha", "lugar", "pais", "continente",
                    "subcontinente", "fuente", "organizador", "email", "link",
                    "subgenero", "tipo_lugar", "contactos"])
    return len(confirmados)


if __name__ == "__main__":
    print("confirmados 2027 (FB directo):", confirmar_2027_fb())
