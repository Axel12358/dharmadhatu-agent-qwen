#!/usr/bin/env python3
"""
Orquestador de fuentes especializadas en psytrance.

Ejecuta todos los scrapers nuevos (fuentes adicionales), clasifica,
filtra no_psy, y exporta un CSV limpio de 100-150 eventos psytrance.

Uso:
    python main_fuentes.py              # Ejecuta todo y exporta eventos_psytrance.csv
    python main_fuentes.py --dry-run    # Solo muestra stats, no exporta
"""

import asyncio
import csv
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from scrapers.event_extractor import EventExtractor
from utils import geo_utils
from utils.helpers import deduplicar_eventos, limpiar_eventos

# Scrapers nuevos (fuentes especializadas)
from scrapers.eventbrite_psy import scrape_eventbrite_psy
from scrapers.reddit_psy import scrape_reddit_psy
from scrapers.psytrance_pl import scrape_psytrance_pl
from scrapers.isratrance import scrape_isratrance
from scrapers.ektoplazm import scrape_ektoplazm
from scrapers.meetup_psy import scrape_meetup_psy

# Scrapers existentes (consolidados en main.py)
from scrapers import scrape_goabase, scrape_songkick
from scrapers import scrape_ra
from scrapers.facebook_mcp import scrape_facebook_events

OUTPUT_LIMPIO = "eventos_psytrance.csv"
OUTPUT_TODOS = "eventos_encontrados.csv"

FUENTES_NO_PSY = {"Resident Advisor"}
_extractor = EventExtractor()

# Fuentes cuyo catálogo ES psytrance por definición (portal especializado)
FUENTES_ESPECIALIZADAS = {"Goabase", "Psytrance.pl", "IsraTrance", "Ektoplazm",
                          "Songkick"}

# Subgéneros válidos de la familia psytrance
SUBGENEROS_PSY = {"psytrance", "goa", "darkpsy", "forest", "hitech", "progressive",
                  "psychedelic", "psychill", "psybient", "fullon", "twilight",
                  "psycore", "suomisaundi", "zenon"}


def fecha_valida(fecha):
    """True si la fecha está en formato YYYY-MM-DD (evento con fecha real)."""
    if not fecha:
        return False
    fecha = str(fecha).strip()
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", fecha))


def link_valido(link):
    """True si el link es una URL http real."""
    if not link:
        return False
    link = str(link).strip()
    return link not in ("N/A", "") and link.startswith("http")


def es_psytrance_real(evento):
    """True si el evento es psytrance de verdad.

    Fuentes especializadas (portal psytrance): todo su catálogo cuenta.
    Fuentes generales: exige subgénero de la familia psytrance.
    """
    if evento.get("fuente") in FUENTES_ESPECIALIZADAS:
        return True
    return evento.get("subgenero") in SUBGENEROS_PSY


def limpiar_calidad(eventos):
    """Filtra ruido: exige fecha + link reales y psytrance real.

    Elimina falsos positivos (posts de discusión, salsa/meditación de Meetup,
    etc.) que antes entraban al CSV 'limpio' solo por no ser 'no_psy'.

    Excepción: eventos de Facebook con link real y subgénero psytrance real
    se conservan aunque la fecha no se haya podido extraer (el enriquecimiento
    de fechas puede fallar en páginas públicas de FB).
    """
    limpios = []
    for ev in eventos:
        if not link_valido(ev.get("link")):
            continue
        if not es_psytrance_real(ev):
            continue
        fecha = ev.get("fecha", "")
        es_facebook = "Facebook" in ev.get("fuente", "")
        if not fecha_valida(fecha):
            if es_facebook and fecha in ("Fecha no disponible", "N/A", ""):
                ev["fecha"] = "N/A"
            else:
                continue
        limpios.append(ev)
    return limpios


def clasificar_eventos(eventos):
    """Añade/rellena subgenero respetando el ya asignado.

    Estrategias en orden:
    1. Respetar subgénero ya asignado (no general).
    2. Para fuentes especializadas (Goabase, Psytrance.pl, etc.) con
       subgénero "general", forzar "psytrance" (su catálogo es psytrance).
    3. Para eventos de Facebook con subgénero "general", forzar "psytrance"
       (fueron encontrados vía keywords psytrance).
    4. Clasificación por texto (nombre + lugar + descripción).
    5. Clasificación por organizador recurrente.
    """
    # Primera pasada: fuentes especializadas con "general" → "psytrance"
    for ev in eventos:
        if ev.get("subgenero") == "general" and ev.get("fuente") in FUENTES_ESPECIALIZADAS:
            ev["subgenero"] = "psytrance"

    # Segunda pasada: Facebook con "general" → "psytrance"
    for ev in eventos:
        if ev.get("subgenero") == "general" and "Facebook" in ev.get("fuente", ""):
            ev["subgenero"] = "psytrance"

    # Tercera pasada: clasificación por texto para los que sigan "general"
    for ev in eventos:
        if ev.get("subgenero") and ev["subgenero"] != "general":
            continue
        texto = " ".join(filter(None, [ev.get("nombre"), ev.get("lugar"),
                                       ev.get("descripcion")]))
        ev["subgenero"] = _extractor.clasificar_subgenero(texto)

    # Cuarta pasada: organizador recurrente
    _aplicar_organizador_recurrente(eventos)

    return eventos


def _aplicar_organizador_recurrente(eventos):
    """Si un organizador tiene ≥2 eventos ya clasificados con un subgénero
    psytrance real, asignar ese subgénero a sus eventos "general"."""
    org_subgeneros = {}
    for ev in eventos:
        org = ev.get("organizador", "").strip()
        sg = ev.get("subgenero", "")
        if not org or org == "N/A" or sg == "general" or sg == "no_psy" or not sg:
            continue
        org_subgeneros.setdefault(org, []).append(sg)

    # Determinar subgénero mayoritario por organizador
    org_dominante = {}
    for org, subs in org_subgeneros.items():
        if len(subs) >= 2:
            dominante = Counter(subs).most_common(1)[0][0]
            org_dominante[org] = dominante

    # Aplicar a eventos "general" de esos organizadores
    for ev in eventos:
        if ev.get("subgenero") != "general":
            continue
        org = ev.get("organizador", "").strip()
        if org in org_dominante:
            ev["subgenero"] = org_dominante[org]


def filtrar_no_psy(eventos):
    """Marca como 'no_psy' eventos general de fuentes no especializadas."""
    for ev in eventos:
        if ev.get("subgenero") == "general" and ev.get("fuente") in FUENTES_NO_PSY:
            ev["subgenero"] = "no_psy"
    return eventos


def clasificar_geo(eventos):
    """Añade continente/subcontinente."""
    for ev in eventos:
        pais = ev.get("pais")
        if pais and pais not in ("N/A", ""):
            geo = geo_utils.clasificar_geo(pais)
            ev.update(geo)
        else:
            ev.update({"continente": "Otro", "subcontinente": "Otro"})
    return eventos


def exportar_csv(eventos, filename):
    """Exporta eventos a CSV en formato estándar."""
    keys = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for ev in eventos:
            link = ev.get("link") or ev.get("url") or "N/A"
            ev_out = dict(ev)
            ev_out["link"] = link
            writer.writerow(ev_out)
    print(f"✅ CSV exportado: {filename} ({len(eventos)} filas)")


def cargar_csv_existente(filename):
    """Carga eventos existentes de un CSV."""
    eventos = []
    try:
        with open(filename, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                eventos.append(dict(row))
        print(f"📥 Cargados {len(eventos)} eventos de {filename}")
    except FileNotFoundError:
        print(f"⚠️ {filename} no encontrado, empezando desde cero")
    return eventos


async def ejecutar_fuentes_nuevas():
    """Ejecuta todos los scrapers de fuentes especializadas."""
    todos = []

    # --- Scrapers síncronos ---
    scrapers_sinc = [
        ("Reddit", scrape_reddit_psy),
        ("Psytrance.pl", scrape_psytrance_pl),
        ("IsraTrance", scrape_isratrance),
        ("Ektoplazm", scrape_ektoplazm),
        ("Meetup", scrape_meetup_psy),
    ]
    for nombre, func in scrapers_sinc:
        try:
            evs = await asyncio.wait_for(asyncio.to_thread(func), timeout=60)
            if evs:
                print(f"  ✅ {nombre}: {len(evs)} eventos")
                todos.extend(evs)
            else:
                print(f"  ⚠️ {nombre}: 0 eventos")
        except asyncio.TimeoutError:
            print(f"  ⚠️ {nombre}: timeout (60s) — omitido")
        except Exception as e:
            print(f"  ❌ {nombre}: {e}")

    # --- Eventbrite (Playwright, opcional) ---
    try:
        evs = await asyncio.wait_for(scrape_eventbrite_psy(), timeout=90)
        if evs:
            print(f"  ✅ Eventbrite: {len(evs)} eventos psytrance")
            todos.extend(evs)
    except asyncio.TimeoutError:
        print("  ⚠️ Eventbrite: timeout (90s) — omitido")
    except Exception as e:
        print(f"  ❌ Eventbrite: {e}")

    # --- Facebook (SERP público + grupos) ---
    # Siempre se ejecuta: max_keywords=15, max_visitas=50, timeout 300s
    try:
        evs = await asyncio.wait_for(
            scrape_facebook_events(max_keywords=15, max_visitas=50),
            timeout=300
        )
        if evs:
            print(f"  ✅ Facebook: {len(evs)} eventos")
            todos.extend(evs)
        else:
            print("  ⚠️ Facebook: 0 eventos")
    except asyncio.TimeoutError:
        print("  ⚠️ Facebook: timeout (300s) — omitido")
    except Exception as e:
        print(f"  ❌ Facebook: {e}")

    return todos


async def ejecutar_fuentes_existentes():
    """Ejecuta los scrapers existentes (main.py)."""
    todos = []

    scrapers = [
        ("Goabase", lambda: scrape_goabase(limit=301)),
        ("Songkick", lambda: scrape_songkick(limit=100)),
        ("RA", scrape_ra),
    ]
    for nombre, func in scrapers:
        try:
            evs = func()
            if evs:
                print(f"  ✅ {nombre}: {len(evs)} eventos")
                todos.extend(evs)
        except Exception as e:
            print(f"  ❌ {nombre}: {e}")

    return todos


async def main(dry_run=False):
    print("=" * 60)
    print("🧘 Dharmadhatu — Orquestador de Fuentes Psytrance")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Cargar CSV existente
    eventos_existentes = cargar_csv_existente(OUTPUT_TODOS)

    # 2. Ejecutar fuentes nuevas
    print("\n🆕 Fuentes especializadas (nuevas)...")
    fuentes_nuevas = await ejecutar_fuentes_nuevas()

    # 3. Ejecutar fuentes existentes solo si no hay CSV
    fuentes_existentes = []
    if not eventos_existentes:
        print("\n📦 Fuentes existentes (consolidadas)...")
        fuentes_existentes = await ejecutar_fuentes_existentes()

    # 4. Consolidar todos
    todos = eventos_existentes + fuentes_nuevas + fuentes_existentes
    print(f"\n📊 Total bruto: {len(todos)} eventos")

    # 5. Limpiar eventos inválidos (fecha, link, lugar)
    print("\n🧹 Validando eventos...")
    antes = len(todos)
    todos = limpiar_eventos(todos)
    print(f"   {antes} → {len(todos)} eventos válidos")

    # 6. Deduplicar
    print("\n🔄 Deduplicando...")
    antes = len(todos)
    todos = deduplicar_eventos(todos)
    print(f"   {antes} → {len(todos)} eventos únicos")

    # 7. Clasificar
    print("\n🏷️ Clasificando subgéneros...")
    todos = clasificar_eventos(todos)

    # 8. Filtrar no_psy
    print("🚫 Filtrando no_psy...")
    todos = filtrar_no_psy(todos)
    psy_count = sum(1 for e in todos if e.get("subgenero") != "no_psy")
    noppsy_count = sum(1 for e in todos if e.get("subgenero") == "no_psy")
    print(f"   Psytrance (clasificación): {psy_count} | No-psy: {noppsy_count}")

    # 9. Limpiar calidad: solo eventos reales (fecha + link + psytrance real)
    print("🧹 Limpiando calidad (fecha + link + psy real)...")
    todos_limpios = limpiar_calidad(todos)
    print(f"   {len(todos)} → {len(todos_limpios)} eventos reales")

    # 9. Geo
    print("🌍 Clasificando geografía...")
    todos_limpios = clasificar_geo(todos_limpios)

    # 10. Exportar
    if not dry_run:
        print("\n💾 Exportando CSVs...")
        exportar_csv(todos_limpios, OUTPUT_TODOS)
        exportar_csv(todos_limpios, OUTPUT_LIMPIO)

        # Stats por fuente
        print("\n📊 Desglose por fuente:")
        fuentes = {}
        for e in todos_limpios:
            f = e.get("fuente", "?")
            fuentes[f] = fuentes.get(f, 0) + 1
        for f, c in sorted(fuentes.items(), key=lambda x: -x[1]):
            print(f"   {f}: {c}")

        # Stats por subgénero
        print("\n🎵 Desglose por subgénero:")
        subs = {}
        for e in todos_limpios:
            s = e.get("subgenero", "?")
            subs[s] = subs.get(s, 0) + 1
        for s, c in sorted(subs.items(), key=lambda x: -x[1]):
            print(f"   {s}: {c}")
    else:
        print("\n🔍 Modo dry-run — sin exportar")

    print("\n✅ Completado")
    return todos


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
