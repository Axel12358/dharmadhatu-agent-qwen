#!/usr/bin/env python3
"""
Ejecuta solo Facebook con parámetros agresivos y consolida con el CSV principal.

Uso:
    python3 run_facebook_only.py              # Ejecuta y consolida
    python3 run_facebook_only.py --dry-run    # Solo muestra stats, no exporta
"""

import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

from scrapers.facebook_mcp import scrape_facebook_events
from utils.helpers import deduplicar_eventos
from main_fuentes import clasificar_eventos, limpiar_calidad

OUTPUT_TODOS = "eventos_encontrados.csv"
OUTPUT_LIMPIO = "eventos_psytrance.csv"


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


async def main(dry_run=False):
    print("=" * 60)
    print("📱 Dharmadhatu — Facebook Only (parámetros agresivos)")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # 1. Cargar CSV existente
    eventos_existentes = cargar_csv_existente(OUTPUT_TODOS)

    # 2. Ejecutar Facebook con parámetros agresivos.
    #    Tres pasadas seguidas: la rotación de países (rotation_state.json)
    #    selecciona países distintos en cada pasada, cubriendo así varios
    #    grupos de países sin saturar una sola ejecución.
    facebook_events = []
    PASADAS = 3
    for pasada in range(1, PASADAS + 1):
        print(f"\n🔍 Facebook pasada {pasada}/{PASADAS} "
              f"(max_keywords=30, max_visitas=60)...")
        try:
            evs = await asyncio.wait_for(
                scrape_facebook_events(max_keywords=30, max_visitas=60),
                timeout=1200
            )
            print(f"  ✅ Facebook pasada {pasada}: {len(evs)} eventos")
            facebook_events.extend(evs)
        except asyncio.TimeoutError:
            print(f"  ⚠️ Facebook pasada {pasada}: timeout (1200s)")
        except Exception as e:
            print(f"  ❌ Facebook pasada {pasada}: {e}")
    print(f"\n📊 Facebook total: {len(facebook_events)} eventos (antes de dedup)")

    # 3. Consolidar: los eventos existentes ya pasaron el control de calidad en
    #    ejecuciones anteriores → se conservan tal cual (nunca restar).
    #    Solo los NUEVOS se clasifican y filtran.
    print(f"\n📊 Total bruto: {len(eventos_existentes)} existentes + "
          f"{len(facebook_events)} nuevos = {len(eventos_existentes) + len(facebook_events)}")

    # 3b. Clasificar y filtrar solo los eventos nuevos de Facebook
    print("\n🏷️ Clasificando nuevos...")
    facebook_events = clasificar_eventos(facebook_events)
    print("🧹 Filtrando calidad de nuevos...")
    antes = len(facebook_events)
    facebook_events = limpiar_calidad(facebook_events)
    print(f"   {antes} → {len(facebook_events)} nuevos reales")

    todos = eventos_existentes + facebook_events

    # 4. Deduplicar (conserva el primero = el ya existente en el CSV)
    print("\n🔄 Deduplicando...")
    antes = len(todos)
    todos = deduplicar_eventos(todos)
    print(f"   {antes} → {len(todos)} eventos únicos")

    # 5. Exportar
    if not dry_run:
        print("\n💾 Exportando CSVs...")
        exportar_csv(todos, OUTPUT_TODOS)
        exportar_csv(todos, OUTPUT_LIMPIO)

        # Stats por fuente
        print("\n📊 Desglose por fuente:")
        fuentes = {}
        for e in todos:
            f = e.get("fuente", "?")
            fuentes[f] = fuentes.get(f, 0) + 1
        for f, c in sorted(fuentes.items(), key=lambda x: -x[1]):
            print(f"   {f}: {c}")
        fb_count = sum(1 for e in todos if "Facebook" in e.get("fuente", ""))

        # Stats por subgénero
        print("\n🎵 Desglose por subgénero:")
        subs = {}
        for e in todos:
            s = e.get("subgenero", "?")
            subs[s] = subs.get(s, 0) + 1
        for s, c in sorted(subs.items(), key=lambda x: -x[1]):
            print(f"   {s}: {c}")
        print(f"\n✅ Total: {len(todos)} | Facebook: {fb_count}")
    else:
        print("\n🔍 Modo dry-run — sin exportar")

    print("\n✅ Completado")
    return todos


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry))
