"""Completa campos N/A en eventos_encontrados.csv con información REAL.

Estrategia sin red (no se cuelga en Tor):
1. Propagación intra-CSV: filas con el mismo `lugar` (o token inicial) ya
   traen pais/continente/subcontinente -> se copian a las filas que faltan.
   Es info real del propio dataset, no inventada.
2. Heurística de tipo_lugar/subgenero a partir del nombre y la URL.
3. email/contactos/fecha quedan para enriquecimiento web (Fase 5) cuando FB
   responda; aquí no se tocan para no meter datos falsos.

Reescribe el CSV completo (preservando las 729 filas y todas las columnas):
es llenar huecos, no restar.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CSV = PROJECT_ROOT / "eventos_encontrados.csv"

_SUBGENEROS = ["darkpsy", "forest", "psychill", "psybient", "fullon",
               "progressive", "goa", "hitech", "twilight", "psycore",
               "suomisaundi", "zenon", "psychedelic", "psytrance", "dark psy",
               "forest psy"]

# Canal de Telegram -> (pais, continente, subcontinente) real, por la escena.
CANAL_PAIS = {
    "phanganparty": ("Tailandia", "Asia", "Sudeste asiático"),
    "afishagoa": ("India", "Asia", "Asia meridional"),
    "themysticrose": ("India", "Asia", "Asia meridional"),
}

# pais (español o local) -> (continente, subcontinente) real.
PAIS_CONTINENTE = {
    "Israel": ("Asia", "Asia occidental"),
    "Austria": ("Europa", "Europa occidental"),
    "Reino Unido": ("Europa", "Europa occidental"),
    "Dinamarca": ("Europa", "Europa septentrional"),
    "Países Bajos": ("Europa", "Europa occidental"),
    "España": ("Europa", "Europa meridional"),
    "Australia": ("Oceanía", "Oceanía"),
    "Francia": ("Europa", "Europa occidental"),
    "India": ("Asia", "Asia meridional"),
    "Alemania": ("Europa", "Europa occidental"),
    "Finlandia": ("Europa", "Europa septentrional"),
    "Poland": ("Europa", "Europa oriental"),
    "Nigeria": ("África", "África occidental"),
    "Uganda": ("África", "África oriental"),
    "Sudan": ("África", "África septentrional"),
    "السودان": ("África", "África septentrional"),
    "Berlin": ("Europa", "Europa occidental"),
}

# Normalización de países: variantes (ES/EN) -> país canónico.
# Se aplica al leer el CSV y antes de escribirlo, para eliminar duplicados
# como España/Spain, Alemania/Germany, Países Bajos/Netherlands.
NORMALIZACION_PAISES = {
    "españa": "España",
    "spain": "España",
    "alemania": "Alemania",
    "germany": "Alemania",
    "países bajos": "Países Bajos",
    "netherlands": "Países Bajos",
    "holanda": "Países Bajos",
    "dutch": "Países Bajos",
    "portugal": "Portugal",
    "méxico": "México",
    "mexico": "México",
    "argentina": "Argentina",
    "brasil": "Brasil",
    "brazil": "Brasil",
    "estados unidos": "Estados Unidos",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "francia": "Francia",
    "france": "Francia",
    "italia": "Italia",
    "italy": "Italia",
    "reino unido": "Reino Unido",
    "united kingdom": "Reino Unido",
    "uk": "Reino Unido",
    "china": "China",
    "japón": "Japón",
    "japan": "Japón",
    "corea del sur": "Corea del Sur",
    "south korea": "Corea del Sur",
    "tailandia": "Tailandia",
    "thailand": "Tailandia",
    "india": "India",
    "canadá": "Canadá",
    "canada": "Canadá",
    "australia": "Australia",
    "new zealand": "Nueva Zelanda",
    "nz": "Nueva Zelanda",
    "suiza": "Suiza",
    "switzerland": "Suiza",
    "austria": "Austria",
    "bélgica": "Bélgica",
    "belgica": "Bélgica",
    "belgium": "Bélgica",
    "holanda": "Países Bajos",
    "dutch": "Países Bajos",
    "españa": "España",
    "spain": "España",
    "alemania": "Alemania",
    "germany": "Alemania",
    "francia": "Francia",
    "france": "Francia",
    "italia": "Italia",
    "italy": "Italia",
    "portugal": "Portugal",
    "méxico": "México",
    "mexico": "México",
    "argentina": "Argentina",
    "brasil": "Brasil",
    "brazil": "Brasil",
    "canadá": "Canadá",
    "canada": "Canadá",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _na(v: str) -> bool:
    return (v or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?")


def _first_word(k: str) -> str:
    parts = k.split()
    return parts[0] if parts else k


def _inferir_tipo(nombre: str, link: str) -> str:
    n = (nombre or "").lower()
    if "festival" in n:
        return "festival"
    if "open air" in n or "open-air" in n:
        return "open air"
    if any(w in n for w in ("beach", "praia", "playa", "strand")):
        return "playa"
    if "club" in n:
        return "club"
    if "rave" in n:
        return "rave"
    if any(w in n for w in ("party", "fiesta")):
        return "fiesta"
    if "gathering" in n:
        return "gathering"
    if "facebook.com/events" in (link or ""):
        return "festival"
    return "evento"


def _inferir_subgenero(nombre: str, actual: str) -> str:
    if not _na(actual):
        return actual
    n = (nombre or "").lower()
    for sg in _SUBGENEROS:
        if sg in n:
            return sg
    return actual


def completar_na() -> dict:
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    cols = list(rows[0].keys())

    # 1) Mapa geo desde filas completas
    geo = {}
    for r in rows:
        if not _na(r["pais"]) and not _na(r["lugar"]):
            k = _norm(r["lugar"])
            if k and k not in geo:
                geo[k] = (r["pais"], r["continente"], r["subcontinente"])

    try:
        from core.geocode import geocodificar
    except Exception:
        geocodificar = None

    antes = {c: sum(1 for r in rows if _na(r.get(c, ""))) for c in cols}
    cambiados = 0

    for r in rows:
        # pais / continente / subcontinente: propagación intra-CSV, luego Nominatim
        if _na(r["pais"]) or _na(r["continente"]) or _na(r["subcontinente"]):
            k = _norm(r["lugar"])
            g = geo.get(k)
            if not g:
                fw = _first_word(k)
                if len(fw) > 3:
                    for key, val in geo.items():
                        if _first_word(key) == fw:
                            g = val
                            break
            if not g and geocodificar is not None and not _na(r["lugar"]):
                g = geocodificar(r["lugar"])
            if g:
                if _na(r["pais"]):
                    r["pais"] = g[0]
                if _na(r["continente"]):
                    r["continente"] = g[1]
                if _na(r["subcontinente"]):
                    r["subcontinente"] = g[2]
                cambiados += 1
            # Fallback por canal de Telegram (el canal revela el país real)
            if _na(r["pais"]):
                low = (r.get("link", "") or "").lower()
                for canal, (p, c, s) in CANAL_PAIS.items():
                    if f"t.me/s/{canal}" in low or f"t.me/{canal}" in low:
                        r["pais"] = p
                        r["continente"] = c
                        r["subcontinente"] = s
                        cambiados += 1
                        break

    # 2) continente/subcontinente derivado del pais (real), corrige propagación
    #    intra-CSV que copió continentes vacíos desde filas incompletas.
    for r in rows:
        if _na(r["continente"]) and not _na(r["pais"]):
            n = _norm(r["pais"])
            for raw, (c, s) in PAIS_CONTINENTE.items():
                if _norm(raw) == n:
                    r["continente"] = c
                    r["subcontinente"] = s
                    cambiados += 1
                    break

        # tipo_lugar por heurística
        if _na(r["tipo_lugar"]):
            t = _inferir_tipo(r.get("nombre", ""), r.get("link", ""))
            if t:
                r["tipo_lugar"] = t
                cambiados += 1

        # subgenero por heurística de nombre
        if _na(r["subgenero"]):
            r["subgenero"] = _inferir_subgenero(r.get("nombre", ""), r["subgenero"])

    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    despues = {c: sum(1 for r in rows if _na(r.get(c, ""))) for c in cols}
    return {"antes": antes, "despues": despues, "cambiados": cambiados}


if __name__ == "__main__":
    res = completar_na()
    print(f"Campos modificados: {res['cambiados']}")
    for c in res["antes"]:
        if res["antes"][c] != res["despues"][c]:
            print(f"  {c:14} {res['antes'][c]} -> {res['despues'][c]}")
