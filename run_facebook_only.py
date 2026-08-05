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

    # 2. Ejecutar Facebook con parámetros agresivos
    print("\n🔍 Ejecutando Facebook (max_keywords=8, max_visitas=15)...")
    try:
        facebook_events = await asyncio.wait_for(
            scrape_facebook_events(max_keywords=8, max_visitas=15),
            timeout=600
        )
        print(f"  ✅ Facebook: {len(facebook_events)} eventos encontrados")
    except asyncio.TimeoutError:
        print("  ⚠️ Facebook: timeout (600s)")
        facebook_events = []
    except Exception as e:
        print(f"  ❌ Facebook: {e}")
        facebook_events = []

    # 3. Consolidar
    todos = eventos_existentes + facebook_events
    print(f"\n📊 Total bruto: {len(todos)} eventos")

    # 4. Deduplicar
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

        # Stats por subgénero
        print("\n🎵 Desglose por subgénero:")
        subs = {}
        for e in todos:
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
