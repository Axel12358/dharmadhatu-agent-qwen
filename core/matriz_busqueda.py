#!/usr/bin/env python3
"""
Matriz de Búsqueda Amplia — genera combinaciones masivas subgénero × ciudad × año × tipo
y las ejecuta vía Tor (serp_tor.py) con dedup global. Mientras más mejor: soporta
presupuestos de 50 a 10k queries por run.

Ejes:
  - 34 sinónimos (SINONIMOS de event_extractor.py) + 14 base
  - 120 ciudades (config/ciudades_120.json)
  - 4 años (2025-2028)
  - 8 tipos (festival, gathering, open air, party, rave, ceremony, ritual, celebration)

Estrategia inteligente:
  - Sampling por peso (país con más eventos tiene más queries)
  - Dedup de dorks ya visitados (historial_improvement_loop.json + cache_dedup.json)
  - Cada query vía core/serp_tor.py (DDG+SearxNG por Tor socks5h, nunca IP real)
  - 1.2s sleep + rotación Tor cada 10 queries

Uso:
  python -m core.matriz_busqueda --presupuesto 200          # 200 queries (16 min)
  python -m core.matriz_busqueda --presupuesto 1000 --dry-run  # solo genera, no ejecuta
  python -m core.matriz_busqueda --presupuesto 5000         # extensivo, ~4h
"""
import argparse
import csv
import itertools
import json
import random
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

CIUDADES_FILE = _PROJECT_ROOT / "config" / "ciudades_120.json"
CSV_FILE = _PROJECT_ROOT / "eventos_encontrados.csv"
HISTORIAL_FILE = _PROJECT_ROOT / "historial_improvement_loop.json"
PENDIENTES_FILE = _PROJECT_ROOT / "combinaciones_pendientes.json"

ANIOS = ["2025", "2026", "2027", "2028"]
TIPOS = ["festival", "gathering", "open air", "party", "rave", "ceremony", "ritual", "celebration"]

try:
    from scrapers.event_extractor import SINONIMOS
    SINONIMOS_LIST = list(SINONIMOS.keys())
except Exception:
    SINONIMOS_LIST = ["psytrance", "goa", "forest", "darkpsy", "progressive", "hitech", "fullon", "psychill", "psybient", "suomisaundi"]


def _cargar_ciudades() -> List[Dict]:
    if not CIUDADES_FILE.exists():
        return []
    data = json.loads(CIUDADES_FILE.read_text(encoding="utf-8"))
    return data.get("ciudades", [])


def _dorks_vistos() -> set:
    vistos = set()
    if HISTORIAL_FILE.exists():
        try:
            data = json.loads(HISTORIAL_FILE.read_text(encoding="utf-8"))
            for entry in data if isinstance(data, list) else data.get("runs", []):
                for d in entry.get("dorks", []) if isinstance(entry, dict) else []:
                    vistos.add(d)
        except Exception:
            pass
    return vistos


def generar_combinaciones(presupuesto: int = 200, semilla: int = 42, evitar_vistos: bool = True) -> List[Dict]:
    ciudades = _cargar_ciudades()
    if not ciudades:
        print("❌ config/ciudades_120.json no encontrado")
        return []

    # Pesos de ciudades (normalizado)
    total_peso = sum(c.get("peso", 20) for c in ciudades)
    # Sinónimos con peso: los top 5 (psytrance, progressive, goa, darkpsy, forest) 60% del presupuesto
    top_sinonimos = ["psytrance", "psy trance", "goa", "goa trance", "progressive", "prog", "darkpsy", "dark psy", "forest", "forest psy"]
    raros = [s for s in SINONIMOS_LIST if s not in top_sinonimos]

    vistos = _dorks_vistos() if evitar_vistos else set()
    random.seed(semilla)
    combinaciones = []
    intentos = 0
    max_intentos = presupuesto * 10

    while len(combinaciones) < presupuesto and intentos < max_intentos:
        intentos += 1
        # 60% top, 30% raro, 10% aleatorio puro
        r = random.random()
        if r < 0.6:
            sinonimo = random.choice(top_sinonimos)
        elif r < 0.9:
            sinonimo = random.choice(raros)
        else:
            sinonimo = random.choice(SINONIMOS_LIST)

        # Ciudad por peso
        ciudad = random.choices(ciudades, weights=[c.get("peso", 20) for c in ciudades], k=1)[0]
        anio = random.choice(ANIOS)
        tipo = random.choice(TIPOS)

        dork = f"site:facebook.com/events {sinonimo} {tipo} {ciudad['ciudad']} {anio}"
        if dork in vistos:
            continue
        vistos.add(dork)
        combinaciones.append({
            "dork": dork,
            "sinonimo": sinonimo,
            "ciudad": ciudad["ciudad"],
            "pais": ciudad["pais"],
            "anio": anio,
            "tipo": tipo,
            "tier": ciudad["tier"],
        })

    pend = {"combinaciones": combinaciones, "total": len(combinaciones), "presupuesto": presupuesto}
    PENDIENTES_FILE.write_text(json.dumps(pend, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"📋 Generadas {len(combinaciones)}/{presupuesto} combinaciones → {PENDIENTES_FILE}")
    # Stats por tier
    from collections import Counter
    print(f"   Por tier: {dict(Counter(c['tier'] for c in combinaciones))}")
    print(f"   Por año: {dict(Counter(c['anio'] for c in combinaciones))}")
    return combinaciones


def ejecutar_combinaciones(combinaciones: List[Dict], dry_run: bool = False) -> int:
    if dry_run:
        print(f"🔍 Dry-run: {len(combinaciones)} dorks generados, no se ejecuta SERP")
        for c in combinaciones[:5]:
            print(f"   - {c['dork']}")
        return 0

    try:
        from core.serp_tor import buscar_serp
        from core.deduplicador import Deduplicador
        from core.tor_pool import rotar_tor
    except Exception as e:
        print(f"❌ Import SERP/Tor falló: {e}")
        return 0

    dedup = Deduplicador()
    # Cargar existentes como vistos
    if CSV_FILE.exists():
        try:
            existentes = list(csv.DictReader(open(CSV_FILE, encoding="utf-8")))
            dedup.registrar_vistos(existentes)
            print(f"📂 CSV: {len(existentes)} existentes registrados en dedup")
        except Exception:
            existentes = []
    else:
        existentes = []

    nuevos_total = []
    t_inicio = time.time()
    for i, combo in enumerate(combinaciones, 1):
        # Throttling mínimo en searxng_local (~0.4s/query) para no saturar
        # motores (Google CSE limita ~100 queries/min). El Tor tiene su
        # propio delay natural (~15s/query).
        elapsed = time.time() - t_inicio
        sleep_min = 0.4 if elapsed / max(i, 1) < 2 else 0
        if sleep_min and i > 1:
            time.sleep(sleep_min)
        print(f"[{i}/{len(combinaciones)}] {combo['dork'][:70]}...", end=" ", flush=True)
        try:
           # Prioridad 1: SearxNG local (Docker, multi-motor, respeta site:fb)
            resultados = buscar_serp(combo["dork"], directo=True)
            hay_fb = any("facebook.com/events" in (r.get("url") or "") for r in resultados)
            if not hay_fb:
                # Prioridad 2: Tor (DDG → SearxNG pública), radio lento
                resultados = buscar_serp(combo["dork"], motores=["ddg", "searxng"])
        except Exception as e:
            print(f"❌ {e}")
            resultados = []

        # Convertir resultados SERP a eventos mínimos
        eventos = []
        for r in resultados[:10]:
            url = r.get("url", "") or r.get("link", "")
            titulo = r.get("title", "") or r.get("titulo", "")
            if "facebook.com/events" not in url.lower():
                continue
            eventos.append({
                "nombre": titulo[:120] or combo["sinonimo"] + " " + combo["tipo"],
                "fecha": f"{combo['anio']}-01-01",
                "lugar": combo["ciudad"],
                "pais": combo["pais"],
                "continente": "",
                "subcontinente": "",
                "fuente": "Facebook (matriz)",
                "organizador": "N/A",
                "email": "N/A",
                "link": url,
                "subgenero": combo["sinonimo"],
                "tipo_lugar": combo["tipo"],
            })

        nuevos = dedup.filtrar_nuevos(eventos)
        if nuevos:
            dedup.registrar_vistos(nuevos)
            nuevos_total.extend(nuevos)
            print(f"✅ {len(nuevos)}/{len(eventos)} nuevos")
        else:
            print(f"— 0 nuevos ({len(eventos)} hallazgos)")

        time.sleep(1.2)
        if i % 10 == 0:
            try:
                rotar_tor()
                print("   🔄 Tor rotado")
            except Exception:
                pass

    if nuevos_total:
        # Consolidar aditivo al CSV bajo lock (no pisar escritores concurrentes)
        try:
            from core.csv_lock import csv_locked_rows
        except Exception:
            from csv_lock import csv_locked_rows
        with csv_locked_rows(CSV_FILE, timeout=180) as (rows, _fn):
            cols = list(rows[0].keys()) if rows else list(nuevos_total[0].keys())
            seen = {r.get("link") for r in rows if r.get("link")}
            agregados = 0
            for ev in nuevos_total:
                if ev.get("link") and ev["link"] in seen:
                    continue
                rows.append(ev)
                seen.add(ev.get("link"))
                agregados += 1
        dedup.guardar()
        print(f"\n💾 {agregados} nuevos añadidos → CSV total {len(rows)}")

    return len(nuevos_total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Matriz de búsqueda amplia")
    parser.add_argument("--presupuesto", type=int, default=200, help="N queries a generar (50-10000)")
    parser.add_argument("--dry-run", action="store_true", help="Solo generar, no ejecutar SERP")
    parser.add_argument("--ejecutar", action="store_true", help="Ejecutar SERP tras generar")
    parser.add_argument("--semilla", type=int, default=42)
    args = parser.parse_args()

    combos = generar_combinaciones(presupuesto=args.presupuesto, semilla=args.semilla)
    if args.ejecutar and not args.dry_run:
        ejecutar_combinaciones(combos, dry_run=False)
    elif args.dry_run:
        ejecutar_combinaciones(combos, dry_run=True)
    else:
        print(f"Tip: añade --ejecutar para lanzar SERP o --dry-run para solo ver")
