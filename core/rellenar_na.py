#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Relleno local (sin red) de N/A en eventos_encontrados.csv.

Llena con datos derivables dentro del propio dataset + geo_cache:
  - pais            : lugar -> pais (mapa auto-aprendido de filas conocidas)
  - continente      : pais -> continente (aprendido + geo_cache)
  - subcontinente   : pais -> subcontinente (aprendido + geo_cache)
  - subgenero       : clasificador por keywords del nombre (vocabulario existente)

Uso (bajo lock, reutilizable desde el loop o CLI):
    python3 core/rellenar_na.py
"""
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_FILE = ROOT / "eventos_encontrados.csv"
GEO_CACHE = ROOT / "geo_cache.json"

# Semilla curada (ciudades/venues de escena sin país en el dataset)
CIUDAD_PAIS_SEED = {
    "Moscow": "Russia", "The Hague": "Netherlands", "Bremen": "Germany",
    "Badalona": "Spain", "Albuquerque": "United States", "Zandvoort": "Netherlands",
    "Richardson": "United States", "Americus": "United States", "Brno": "Czech Republic",
    "Mons": "Belgium", "Ostrowiec Swietokrzyski": "Poland", "Topeka": "United States",
    "Arlee": "United States", "Everson": "United States", "Karlsruhe": "Germany",
    "Saarbrücken": "Germany", "Tramm": "Germany", "Maryville": "United States",
    "Hatfield": "United Kingdom", "Kallnach": "Switzerland", "Crozet": "France",
    "Harrisburg": "United States", "Costa Da Caparica": "Portugal", "Desterro": "Brazil",
    "Paulo": "Brazil",
    # clúster Lisboa (venues de la escena lisboeta)
    "Lisboa Ao Vivo": "Portugal", "LX Factory": "Portugal",
    "Collect LX Factory": "Portugal", "Cosmos Campolide": "Portugal",
    "Lux Fragil": "Portugal", "Kømplex Lisbon": "Portugal",
    "Anfiteatro de Pedra": "Portugal", "Escala25": "Portugal",
    "Papoila Bar": "Portugal", "Quinta Mira Rio": "Portugal",
    "o Bom, o Mau e o Vilão": "Portugal",
}

PAIS_GEO_SEED = {
    "Russia": ("Europa", "Europa oriental"),
    "Brazil": ("América del Sur", "América del Sur"),
    "Belgium": ("Europa", "Europa occidental"),
    "Czech Republic": ("Europa", "Europa Central"),
    "Poland": ("Europa", "Europa Central"),
    "Portugal": ("Europa", "Europa meridional"),
    "Switzerland": ("Europa", "Europa occidental"),
    "United Kingdom": ("Europa", "Europa occidental"),
    "United States": ("América del Norte", "América del Norte"),
}


def _clave(r):
    return r.get("link", "") or f"{r.get('nombre','')}|{r.get('fecha','')}"

SUBGENRE_KEYWORDS = {
    "dark psy": ["dark psy", "darkpsy", "dark", "ritual", "nocturnal", "shadow", "void", "abyss", "cloak", "black"],
    "forest": ["forest", "jungle", "woods", "nature", "organic", "roots", "tribal", "trees"],
    "twilight": ["twilight", "dusk", "sunset"],
    "goa trance": ["goa", "cosmic", "celestial", "spiritual", "mantra", "om "],
    "fullon": ["fullon", "full-on", "full on", "sunrise", "power", "anthem", "tribe", "magic"],
    "hitech": ["hitech", "hi-tech", "high tech", "technoid", "psytech", "cyber", "neuro"],
    "psycore": ["psycore", "core", "terror", "hardcore", "hitechcore", "darkcore"],
    "progressive": ["progressive", "prog trance", "zenon", "flow", "hypnotic", "mental", "melodic"],
    "psybient": ["psybient", "ambient", "psydub", "chill", "dub", "downtempo", "psychill", "suomi"],
    "psy trance": ["psy", "trance", "party", "gathering", "celebration", "festival", "rave", "ceremony"],
}


def _clave(r):
    return r.get("link", "") or f"{r.get('nombre','')}|{r.get('fecha','')}"


def _classificar_subgenero(nombre):
    n = (nombre or "").lower()
    scores = {}
    for genero, kws in SUBGENRE_KEYWORDS.items():
        scores[genero] = sum(1 for kw in kws if kw in n)
    mejor = max(scores, key=scores.get)
    return mejor if scores[mejor] > 0 else "psytrance"


def _aprender_geo(rows):
    """Construye mapas lugar->pais y pais->(continente, subcontinente)."""
    # Seeds curados primero (mayor precedencia; aprendido no los pisa)
    lugar_pais = {}
    pais_geo = {p: (Counter([c]), Counter([s])) for p, (c, s) in PAIS_GEO_SEED.items()}
    aprendido = {}
    for r in rows:
        lug = (r.get("lugar", "") or "").strip()
        pai = (r.get("pais", "") or "").strip()
        cont = (r.get("continente", "") or "").strip()
        sub = (r.get("subcontinente", "") or "").strip()
        if lug and pai and pai.lower() not in ("n/a",):
            aprendido.setdefault(lug, Counter())[pai] += 1
        if pai and pai.lower() not in ("n/a",):
            geo = pais_geo.setdefault(pai, [Counter(), Counter()])
            if cont and cont.lower() != "n/a":
                geo[0][cont] += 1
            if sub and sub.lower() != "n/a":
                geo[1][sub] += 1
    aprendido = {k: v.most_common(1)[0][0] for k, v in aprendido.items()}
    lugar_pais = {**CIUDAD_PAIS_SEED, **aprendido}
    pais_geo = {k: (v[0].most_common(1)[0][0] if v[0] else "",
                    v[1].most_common(1)[0][0] if v[1] else "")
                for k, v in pais_geo.items()}
    # geo_cache: city -> [pais, continente, subcontinente]
    try:
        gc = json.load(open(GEO_CACHE, encoding="utf-8"))
        for city, (pai, cont, sub) in gc.items():
            city = city.strip()
            pai = (pai or "").strip()
            if city and pai and pai.lower() != "n/a":
                lugar_pais.setdefault(city, pai)
            if pai and pai.lower() != "n/a":
                pais_geo.setdefault(pai, ("", ""))
                cont = (cont or "").strip()
                sub = (sub or "").strip()
                if cont and cont.lower() != "n/a" and not pais_geo[pai][0]:
                    pais_geo[pai] = (cont, pais_geo[pai][1] or sub)
                if sub and sub.lower() != "n/a" and not pais_geo[pai][1]:
                    pais_geo[pai] = (pais_geo[pai][0], sub)
    except Exception:
        pass
    return lugar_pais, pais_geo


def rellenar_na_locales(rows):
    """Aplica rellenos locales. Devuelve dict clave -> {campo: valor} a aplicar."""
    lugar_pais, pais_geo = _aprender_geo(rows)
    cambios = {}
    for r in rows:
        k = _clave(r)
        pai = (r.get("pais", "") or "").strip()
        lug = (r.get("lugar", "") or "").strip()
        cont = (r.get("continente", "") or "").strip()
        sub = (r.get("subcontinente", "") or "").strip()
        sg = (r.get("subgenero", "") or "").strip()
        upd = {}
        if (not pai or pai.lower() == "n/a") and lug and lug.lower() != "n/a":
            p = lugar_pais.get(lug)
            if p:
                upd["pais"] = p
                pai = p
        if pai and pai.lower() != "n/a":
            if (not cont or cont.lower() == "n/a") and pais_geo.get(pai, ("", ""))[0]:
                upd["continente"] = pais_geo[pai][0]
            if (not sub or sub.lower() == "n/a") and pais_geo.get(pai, ("", ""))[1]:
                upd["subcontinente"] = pais_geo[pai][1]
        if not sg or sg.lower() == "n/a":
            upd["subgenero"] = _classificar_subgenero(r.get("nombre", "") or "")
        if upd:
            cambios[k] = upd
    return cambios


def main():
    sys.path.insert(0, str(ROOT))
    from core.csv_lock import csv_locked_rows
    with csv_locked_rows(CSV_FILE) as (rows, _fn):
        cambios = rellenar_na_locales(rows)
        n = 0
        for r in rows:
            upd = cambios.get(_clave(r))
            if upd:
                for campo, valor in upd.items():
                    r[campo] = valor
                n += 1
        print(f"✅ rellenar_na: {n} filas actualizadas ({sum(len(v) for v in cambios.values())} campos)")


if __name__ == "__main__":
    main()