#!/usr/bin/env python3
"""
Auto-Optimizer — Analiza scrapers y guarda la mejor config.

NO ejecuta scrapers (sería lento). En su lugar:
  1. Lee CSV actual → qué fuentes ya aportan
  2. Evalúa qué scrapers están disponibles (import check)
  3. Prueba combinatorias de subsets con scoring
  4. Guarda mejor_config.json

Uso:
  python3 core/auto_optimizer.py              # analítica + scoring
  python3 core/auto_optimizer.py --aplicar    # aplica mejor config
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

CONFIG_FILE = Path(_PROJECT_ROOT) / "config_modulos.json"
BEST_CONFIG_FILE = Path(_PROJECT_ROOT) / "mejor_config.json"
OPTIMIZER_HISTORY = Path(_PROJECT_ROOT) / "optimizer_history.json"
CSV_FILE = Path(_PROJECT_ROOT) / "eventos_encontrados.csv"

# Todos los scrapers conocidos y sus características
SCRAPER_META = {
    # Estáticos (rápidos, sin Tor)
    "goabase":              {"tipo": "estatico", "costo": 1},
    "songkick":             {"tipo": "estatico", "costo": 1},
    "resident_advisor":     {"tipo": "estatico", "costo": 1},
    "psytrance_pl":         {"tipo": "estatico", "costo": 1},
    "ektoplazm":            {"tipo": "estatico", "costo": 1},
    "psynews":              {"tipo": "estatico", "costo": 1},
    "isratrance":           {"tipo": "estatico", "costo": 1},
    # SERP (Tor, rápidos)
    "reddit_psy":           {"tipo": "serp", "costo": 2},
    "meetup_psy":           {"tipo": "serp", "costo": 2},
    "facebook_dorks":       {"tipo": "serp", "costo": 2},
    "instagram_dorks":      {"tipo": "serp", "costo": 2},
    # Scraping directo (Tor)
    "edmdancedirectory":    {"tipo": "scraping", "costo": 3},
    "psytrancefestivals_tv":{"tipo": "scraping", "costo": 3},
    "psymedia":             {"tipo": "scraping", "costo": 3},
    "setline":              {"tipo": "scraping", "costo": 3},
    "psychill_space":       {"tipo": "scraping", "costo": 3},
    # LLM / Agentes
    "mcp_organizador":      {"tipo": "agente", "costo": 2},
    "dorks_resultados":     {"tipo": "agente", "costo": 1},
    # Pesados (login)
    "telegram":             {"tipo": "pesado", "costo": 4},
    "facebook_mcp":         {"tipo": "pesado", "costo": 5},
    "instagram_scraper":    {"tipo": "pesado", "costo": 5},
    "fb_playwright_tor":    {"tipo": "pesado", "costo": 5},
}

SCRAPERS_LIGEROS = [k for k, v in SCRAPER_META.items() if v["costo"] <= 3]


def _load_json(path: Path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data):
    tmp = str(path) + ".tmp"
    Path(tmp).write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    os.replace(tmp, str(path))


def _analizar_csv() -> Dict[str, Any]:
    """Analiza el CSV actual: eventos por fuente, emails, géneros."""
    if not CSV_FILE.exists():
        return {"total": 0, "por_fuente": {}, "por_genero": {}, "emails": 0}

    try:
        with open(CSV_FILE, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return {"total": 0, "por_fuente": {}, "por_genero": {}, "emails": 0}

    por_fuente = {}
    por_genero = {}
    emails = 0
    for r in rows:
        src = r.get("fuente", "Unknown")
        por_fuente[src] = por_fuente.get(src, 0) + 1
        gen = r.get("subgenero", "N/A")
        por_genero[gen] = por_genero.get(gen, 0) + 1
        if r.get("email", "").strip():
            emails += 1

    return {
        "total": len(rows),
        "por_fuente": por_fuente,
        "por_genero": por_genero,
        "emails": emails,
    }


def _scrapers_disponibles() -> Set[str]:
    """Verifica qué scrapers son importables y tienen función scrape_*."""
    disponibles = set()
    import importlib
    for scraper in SCRAPER_META:
        try:
            mod = importlib.import_module(f"scrapers.{scraper}")
            # Buscar cualquier función scrape_* en el módulo
            if any(fn.startswith("scrape_") for fn in dir(mod)):
                disponibles.add(scraper)
        except Exception:
            pass
    return disponibles


def _score_config(activos: Dict[str, bool], csv_stats: Dict) -> float:
    """
    Scoring de configuración — métrica orientada a CALIDAD, no volumen.
    - Penaliza no_psy (no premia volumen de basura)
    - Premia subgéneros psytrance reales
    - Premia cobertura de emails (contactos)
    - Penaliza scrapers pesados (FB/IG con login)
    """
    activos = [k for k, v in activos.items() if v]
    score = 0.0

    # Métricas del CSV
    total = csv_stats.get("total", 0)
    por_genero = csv_stats.get("por_genero", {})
    emails = csv_stats.get("emails", 0)
    no_psy = por_genero.get("no_psy", 0)
    n_a = por_genero.get("N/A", 0)
    psy_real = total - no_psy - n_a  # eventos con subgénero real

    # Puntos por calidad: solo subgéneros psytrance reales (+1 cada uno)
    score += psy_real * 1.0

    # Penalización por no_psy (-1 cada uno)
    score -= no_psy * 1.0

    # Penalización por N/A (-0.5 cada uno)
    score -= n_a * 0.5

    # Bonus por cobertura de emails (+0.5 por evento con email)
    score += emails * 0.5

    # Puntos por aporte histórico (qué fuentes aportan al CSV)
    for scraper in activos:
        for fuente_csv, count in csv_stats.get("por_fuente", {}).items():
            if scraper.lower() in fuente_csv.lower():
                score += count * 0.3  # menos peso que antes (0.5 -> 0.3)
                break

    # Puntos por diversidad de tipos
    tipos = set()
    for s in activos:
        tipos.add(SCRAPER_META.get(s, {}).get("tipo", "otro"))
    score += len(tipos) * 10

    # Penalización por scrapers pesados (FB/IG con login)
    for s in activos:
        if SCRAPER_META.get(s, {}).get("costo", 0) >= 5:
            score -= 5

    # Bonus por tener mix de estáticos + SERP
    tiene_estatico = any(SCRAPER_META.get(s, {}).get("tipo") == "estatico" for s in activos)
    tiene_serp = any(SCRAPER_META.get(s, {}).get("tipo") == "serp" for s in activos)
    if tiene_estatico and tiene_serp:
        score += 15

    # Bonus por plugins nuevos (no en config actual)
    config_actual = _load_json(CONFIG_FILE, {})
    activos_actual = config_actual.get("activos", {})
    nuevos = sum(1 for s in activos if activos_actual.get(s) is not True)
    score += nuevos * 3

    return round(score, 2)


def optimizar() -> Dict[str, Any]:
    """
    Analítica de optimización.
    Compara config actual con alternativas y guarda la mejor.
    """
    print("=" * 60)
    print("🔧 Auto-Optimizer — Dharmadhatu Bot")
    print(f"📅 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    csv_stats = _analizar_csv()
    disponibles = _scrapers_disponibles()

    print(f"\n📊 CSV actual: {csv_stats['total']} eventos, "
          f"{csv_stats['emails']} emails")
    print(f"📁 Fuentes: {', '.join(sorted(csv_stats['por_fuente'].keys()))}")
    print(f"🔌 Scrapers disponibles: {len(disponibles)}/{len(SCRAPER_META)}")

    # Config actual
    config_actual = _load_json(CONFIG_FILE, {})
    activos_actual = config_actual.get("activos", {})
    n_actual = sum(1 for v in activos_actual.values() if v)

    print(f"\n⚙️  Config actual: {n_actual} scrapers activos")
    for s, a in activos_actual.items():
        if a and s in disponibles:
            tipo = SCRAPER_META.get(s, {}).get("tipo", "?")
            print(f"   ✅ {s} ({tipo})")

    # Generar candidatos
    candidatos = []

    # 1. Actual
    candidatos.append(("actual", dict(activos_actual)))

    # 2. Solo ligeros
    todos_ligeros = {s: (s in disponibles and s in SCRAPERS_LIGEROS)
                     for s in SCRAPER_META}
    candidatos.append(("solo_ligeros", todos_ligeros))

    # 3. Mix óptimo: estáticos + SERP
    mix_opt = {}
    for s in SCRAPER_META:
        tipo = SCRAPER_META[s]["tipo"]
        mix_opt[s] = s in disponibles and tipo in ("estatico", "serp")
    candidatos.append(("mix_estatico_serp", mix_opt))

    # 4. Todos los disponibles
    todos = {s: (s in disponibles) for s in SCRAPER_META}
    candidatos.append(("todos", todos))

    # 5. Sin pesados
    sin_pesados = {s: (s in disponibles and SCRAPER_META[s]["costo"] < 5)
                   for s in SCRAPER_META}
    candidatos.append(("sin_pesados", sin_pesados))

    # Evaluar
    print(f"\n{'─' * 60}")
    resultados = []
    mejor_score = -999
    mejor = None

    for nombre, activos in candidatos:
        n = sum(1 for v in activos.values() if v)
        score = _score_config(activos, csv_stats)
        tipos = set(SCRAPER_META.get(s, {}).get("tipo", "?")
                    for s, v in activos.items() if v)

        print(f"  {nombre:25s} | {n:2d} scrapers | score {score:7.1f} | "
              f"tipos: {', '.join(sorted(tipos))}")

        resultados.append({
            "nombre": nombre,
            "activos": activos,
            "score": score,
            "n_scrapers": n,
        })

        if score > mejor_score:
            mejor_score = score
            mejor = (nombre, activos, score)

    # Guardar mejor
    if mejor:
        best_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nombre": mejor[0],
            "configuracion": mejor[1],
            "score": mejor[2],
            "csv_stats": {
                "total": csv_stats["total"],
                "emails": csv_stats["emails"],
                "por_fuente": csv_stats["por_fuente"],
            },
        }
        _save_json(BEST_CONFIG_FILE, best_data)
        print(f"\n{'=' * 60}")
        print(f"🏆 Mejor: {mejor[0]} (score {mejor[2]})")
        print(f"💾 Guardado: {BEST_CONFIG_FILE}")

    # Historial
    historial = _load_json(OPTIMIZER_HISTORY, {"runs": []})
    historial.setdefault("runs", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mejor": mejor[0] if mejor else None,
        "score": mejor_score,
        "csv_total": csv_stats["total"],
    })
    historial["runs"] = historial["runs"][-50:]
    _save_json(OPTIMIZER_HISTORY, historial)

    return {"mejor": mejor, "resultados": resultados}


def aplicar_mejor_config():
    """Aplica mejor_config.json a config_modulos.json."""
    best = _load_json(BEST_CONFIG_FILE)
    if not best:
        print("❌ No hay mejor config. Ejecuta sin --aplicar primero.")
        return

    config_actual = _load_json(CONFIG_FILE, {"activos": {}, "proxy": {}})
    nuevos = best.get("configuracion", {})

    # Merge: mantener los que no están en optimización
    for scraper, activo in config_actual.get("activos", {}).items():
        if scraper not in nuevos:
            nuevos[scraper] = activo

    config_actual["activos"] = nuevos
    _save_json(CONFIG_FILE, config_actual)
    print(f"✅ config_modulos.json actualizado (score {best.get('score')})")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--aplicar", action="store_true")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset:
        _save_json(OPTIMIZER_HISTORY, {"runs": []})
        if BEST_CONFIG_FILE.exists():
            BEST_CONFIG_FILE.unlink()
        print("🔄 Historial limpiado")
    elif args.aplicar:
        aplicar_mejor_config()
    else:
        optimizar()
