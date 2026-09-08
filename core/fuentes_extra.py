"""Fase 4 — Más fuentes de eventos psytrance vía dorks multi-motor (Tor).

Amplía la captura más allá de Facebook: Instagram, SoundCloud/Mixcloud,
canales de Telegram y blogs/RSS de la escena. Reusa el buscador multi-motor
de Fase 3 (DDG-lite -> Bing/Startpage/Mojeek/SearxNG por pool Tor), que ya
es robusto. Cada hallazgo se fusiona al CSV principal de forma aditiva (por
link), dejando los campos de geocodificación/enriquecimiento para las fases
5-A/5-B posteriores.
"""
from __future__ import annotations

import csv
import re
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CSV = PROJECT_ROOT / "eventos_encontrados.csv"
COLS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
        "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar",
        "contactos"]

# (fuente, dork). Sin comillas: DDG-lite solo devuelve así; los otros motores
# refinan con site:.
FUENTES_DORKS = [
    ("instagram", "site:instagram.com/p psytrance festival 2027"),
    ("instagram", "site:instagram.com/p goatrance open air 2027"),
    ("telegram", "site:t.me psytrance festival 2027"),
    ("telegram", "site:t.me/s psytrance 2027"),
    ("soundcloud", "site:soundcloud.com psytrance live 2027"),
    ("soundcloud", "site:soundcloud.com/goa trance set 2027"),
    ("rss", "site:psynews.org psytrance event 2027"),
    ("rss", "site:psytrance.com festival 2027"),
]


def _buscar():
    from scrapers.facebook_dorks import _buscar_dork_multimotor_tor
    return _buscar_dork_multimotor_tor


def _buscar_segura(dork: str, timeout: int, tope: int = 40):
    box = {}

    def _w():
        try:
            box["r"] = _buscar()(dork, timeout)
        except Exception:
            box["r"] = []
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(tope)
    return box.get("r", [])


def _link_util(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in ("facebook.com/events", "instagram.com",
                                "t.me", "soundcloud.com", "psynews.org",
                                "psytrance.com"))


def _merge(rows_new: list[dict]) -> int:
    if not rows_new:
        return 0
    exist = list(csv.DictReader(open(CSV, encoding="utf-8"))) if CSV.exists() else []
    vistos = {r.get("link") for r in exist}
    cols = list(exist[0].keys()) if exist else COLS
    added = 0
    for r in rows_new:
        if r["link"] and r["link"] not in vistos:
            exist.append(r)
            vistos.add(r["link"])
            added += 1
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(exist)
    return added


def recolectar_fuentes(max_por_fuente: int = 3) -> int:
    """Recolecta eventos de fuentes extra y los fusiona al CSV (aditivo).

    Fusiona DESPUÉS DE CADA DORK para no perder avance si el subprocess se
    mata por wall-clock (Tor lento).
    """
    total = 0
    for fuente, dork in FUENTES_DORKS:
        nuevos: list[dict] = []
        res = _buscar_segura(dork, 15)
        for hit in res[:max_por_fuente * 3]:
            url = hit.get("url", "")
            if not _link_util(url):
                continue
            nuevos.append({
                "nombre": (hit.get("titulo", "") or "")[:120],
                "fecha": "", "lugar": "", "pais": "", "continente": "",
                "subcontinente": "", "fuente": f"fuentes_extra:{fuente}",
                "organizador": "", "email": "", "link": url,
                "subgenero": "", "tipo_lugar": "", "contactos": "",
            })
        total += _merge(nuevos)
    return total


if __name__ == "__main__":
    print("Fase4 nuevos:", recolectar_fuentes())
