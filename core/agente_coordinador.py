#!/usr/bin/env python3
"""
Agente Coordinador — Sistema Multiagente Ligero (Dharmadhatu Bot v5).

- Ejecuta subagentes en paralelo con ThreadPoolExecutor (máx 3 simultáneos).
- NO usa semáforo global bloqueante que cause cuelgues; cada subagente usa
  future.result(timeout=...) con cancelación y siempre se libera en finally.
- Cada subagente tiene su propio timeout definido en config_modulos.json.
- Recopilación de resultados, deduplicación y supervisión de álgebra lineal.

Principio: siempre sumar, nunca restar. Si un subagente falla, los demás continúan.
Es idempotente: re-ejecutar no duplica eventos en el CSV.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.deduplicador import Deduplicador
from core.task_queue import TaskQueue

from scrapers import (
    scrape_goabase, scrape_songkick, scrape_ra,
    scrape_facebook_events,
    scrape_reddit_psy, scrape_psytrance_pl, scrape_isratrance,
    scrape_ektoplazm, scrape_meetup_psy
)
from scrapers.telegram.scraper import scrape_telegram_events

# Mapeo de nombres de subagentes a funciones reales
FUNCION_SUBAGENTES: Dict[str, Callable[[], Dict]] = {
    "goabase": scrape_goabase,
    "resident_advisor": scrape_ra,
    "facebook_mcp": lambda: asyncio.run(scrape_facebook_events()),
    "facebook_events_from_groups": lambda: asyncio.run(scrape_facebook_events()),
    "telegram": scrape_telegram_events,
    "reddit": scrape_reddit_psy,
    "psytrance_pl": scrape_psytrance_pl,
    "isratrance": scrape_isratrance,
    "ektoplazm": scrape_ektoplazm,
    "meetup": scrape_meetup_psy,
}

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

OUTPUT_TODOS = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")
METRICAS_FILE = str(Path(PROJECT_ROOT) / "metricas_agentes.json")
ESTADO_FILE = str(Path(PROJECT_ROOT) / "estado_agentes.json")
QUEUE_FILE = str(Path(PROJECT_ROOT) / "task_queue_state.json")

TIMEOUT_DEFECTO = 300

# Timeouts por tipo de subagente (segundos)
TIMEOUTS_POR_AGENTE = {
    "resident_advisor": 300,
    "facebook_mcp": 30,
    "facebook_events_from_groups": 30,
    "telegram": 120,
    "reddit": 3,
    "psytrance_pl": 3,
    "isratrance": 3,
    "ektoplazm": 3,
    "meetup": 3,
}


def _leer_csv(filename: str) -> List[Dict]:
    if not Path(filename).exists():
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (IOError, csv.Error):
        return []


def _escribir_csv(filename: str, eventos: List[Dict]) -> None:
    tmp = filename + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar"
        ], extrasaction="ignore")
        writer.writeheader()
        for ev in eventos:
            out = dict(ev)
            out["link"] = ev.get("link") or ev.get("url") or "N/A"
            writer.writerow(out)
    os.replace(tmp, filename)


def _normalizar_evento(ev: Dict) -> Dict:
    ev = dict(ev)
    if not ev.get("link") and ev.get("url"):
        ev["link"] = ev["url"]
    ev.setdefault("fuente", "Desconocida")
    return ev


def _guardar_json(filename: str, data: Any) -> None:
    try:
        tmp = filename + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, filename)
    except (IOError, OSError):
        pass


def _leer_json(filename: str) -> Any:
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _timeout_por_nombre(nombre: str) -> int:
    return TIMEOUTS_POR_AGENTE.get(nombre, TIMEOUT_DEFECTO)


def _leer_subagentes_activos(activos: Optional[List[str]] = None) -> List[Dict]:
    """Carga la lista de subagentes, filtrando por los activos en config_modulos.json."""
    _SUBAGENTES_DEFECTO = [
        {"nombre": "resident_advisor", "funcion": "scrape_resident_advisor", "usa_tor": True, "timeout": 90},
        {"nombre": "telegram", "funcion": "scrape_telegram", "usa_tor": True, "timeout": 120},
        {"nombre": "reddit", "funcion": "scrape_reddit", "usa_tor": True, "timeout": 4},
        {"nombre": "psytrance_pl", "funcion": "scrape_psytrance_pl", "usa_tor": True, "timeout": 6},
        {"nombre": "isratrance", "funcion": "scrape_isratrance", "usa_tor": True, "timeout": 4},
        {"nombre": "ektoplazm", "funcion": "scrape_ektoplazm", "usa_tor": True, "timeout": 4},
        {"nombre": "meetup", "funcion": "scrape_meetup", "usa_tor": True, "timeout": 4},
    ]

    config_path = Path(PROJECT_ROOT) / "config_modulos.json"
    config_activos: Dict[str, bool] = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                config_activos = json.load(f).get("activos", {})
        except Exception:
            pass

    registro = [s for s in _SUBAGENTES_DEFECTO if s["nombre"] in config_activos]
    if activos:
        registro = [ag for ag in registro if ag["nombre"] in activos]
    if not registro:
        registro = _SUBAGENTES_DEFECTO[:]
    return registro


def _obtener_funcion(nombre: str) -> Callable[[], Dict]:
    """Obtiene la función real desde el mapeo FUNCION_SUBAGENTES."""
    return FUNCION_SUBAGENTES.get(nombre, lambda: {"eventos": []})


def _ejecutar_subagente(ag: Dict) -> Dict:
    """Ejecuta un subagente. El timeout es impuesto por el caller
    mediante fut.result(timeout=...). No usa semáforo global ni señales."""
    nombre = ag["nombre"]
    funcion = _obtener_funcion(nombre)

    resultado = {"eventos": [], "error": None, "tiempo": 0.0}
    t0 = time.time()

    try:
        res = funcion()
        if isinstance(res, dict):
            resultado["eventos"] = res.get("eventos", [])
        elif isinstance(res, list):
            resultado["eventos"] = res
        else:
            resultado["eventos"] = []
        resultado["tiempo"] = round(time.time() - t0, 2)
        return resultado
    except Exception as e:
        print(f"  ❌ Error en {nombre}: {str(e)[:60]}")
        resultado["error"] = str(e)
        resultado["tiempo"] = round(time.time() - t0, 2)
        return resultado


def ejecutar_agentes(activos: Optional[List[str]] = None,
                     max_simultaneos: int = 5,
                     dry_run: bool = False) -> int:
    """Ejecuta los subagentes en paralelo y devuelve el nº de eventos NUEVOS
    añadidos al CSV (aditivo, no duplica; 0 si ya estaban todos vistos).

    - `activos`: subconjunto de subagentes a ejecutar (None = todos).
    - `max_simultaneos`: nº máximo de subagentes concurrentes.
    - `dry_run`: no escribe CSV ni caché (solo valida el flujo).
    """
    registro = _leer_subagentes_activos(activos)
    if not registro:
        print("⚠️ No hay subagentes configurados.")
        return 0

    print("=" * 60)
    print("🤖 Agente Coordinador — Sistema Multiagente Ligero")
    print("📅 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)
    print(f"Subagentes ({len(registro)}): " + ", ".join(a["nombre"] for a in registro))
    print(f"Paralelismo máx: {max_simultaneos}")

    t_inicio = time.time()
    dedup = Deduplicador()
    cola = TaskQueue(archivo=QUEUE_FILE)

    # Registrar eventos ya consolidados como vistos (no se re-suman)
    existentes = _leer_csv(OUTPUT_TODOS)
    dedup.registrar_vistos(existentes)

    # Encolar subagentes (persistente, idempotente)
    for ag in registro:
        cola.encolar(ag["nombre"],
                     timeout=ag.get("timeout", TIMEOUT_DEFECTO),
                     prioridad=ag.get("prioridad", 5),
                     usa_tor=bool(ag.get("usa_tor", False)))

    metricas_por_agente: Dict[str, Dict[str, Any]] = {}
    todos_eventos: List[Dict] = []

    with ThreadPoolExecutor(max_workers=max_simultaneos) as pool:
        futuros = {}
        for ag in registro:
            timeout = _timeout_por_nombre(ag["nombre"])
            futuros[ag["nombre"]] = (
                pool.submit(_ejecutar_subagente, ag),
                timeout,
                time.time())

        for nombre, (fut, timeout, t_submit) in futuros.items():
            entrada = {
                "subagente": nombre,
                "timeout": timeout,
                "exito": False,
                "eventos": 0,
                "tiempo": 0.0,
                "error": None,
                "detalle": {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            t_ag = t_submit
            try:
                resultado = fut.result(timeout=timeout)
                evs = [dict(e) for e in (resultado or {}).get("eventos", []) or []]
                evs_normalizados = [_normalizar_evento(e) for e in evs]
                entrada["exito"] = True
                entrada["eventos"] = len(evs_normalizados)
                entrada["detalle"] = dict(resultado.get("detalle", {}) or {})

                todos_eventos.extend(evs_normalizados)
                estado = "✅" if evs_normalizados else "⚠️"
                print(f"  {estado} {nombre}: {len(evs_normalizados)} eventos "
                      f"({entrada['detalle']})")
            except FutTimeout:
                entrada["error"] = "timeout"
                print(f"  ⚠️ {nombre}: timeout ({timeout}s) — omitido")
            except Exception as e:
                entrada["error"] = f"{type(e).__name__}: {e}"
                print(f"  ❌ {nombre}: {entrada['error']}")
            finally:
                entrada["tiempo"] = round(time.time() - t_ag, 2)
                metricas_por_agente[nombre] = entrada
                cola.marcar_completada(nombre,
                                       resultado={"eventos": entrada["eventos"],
                                                  "tiempo": entrada["tiempo"]},
                                       error=entrada["error"])
    cola.guardar()

    print(f"\n📊 Total bruto recolectado: {len(todos_eventos)} eventos")

    # Deduplicación global: solo eventos no vistos (ni en caché ni en CSV)
    nuevos = dedup.filtrar_nuevos(todos_eventos)
    print(f"🔄 Nuevos tras dedup global: {len(nuevos)}")

    # Re-lectura fresca + escritura ATÓMICAS bajo lock (core/csv_lock).
    # Sin esto se SOBRESCRIBE el enriquecimiento concurrente con el snapshot
    # stale leído al inicio del ciclo. Solo se añaden eventos nuevos.
    from core.csv_lock import csv_locked_rows
    with csv_locked_rows(OUTPUT_TODOS) as (frescas, _fn):
        vistos = {(r.get('link', '') or f"{r.get('nombre','')}|{r.get('fecha','')}") for r in frescas}
        add = []
        for e in nuevos:
            k = (e.get('link', '') or f"{e.get('nombre','')}|{e.get('fecha','')}")
            if k and k not in vistos:
                vistos.add(k)
                add.append(e)
        if not dry_run:
            frescas.extend(add)
            consolidados = list(frescas)
        else:
            consolidados = list(frescas) + add
    print(f"📈 Total consolidado: {len(consolidados) - len(add)} + {len(add)} = {len(consolidados)}")

    if not dry_run:
        dedup.registrar_vistos(nuevos)
        dedup.guardar()
        print(f"💾 CSV escrito bajo lock: {OUTPUT_TODOS} ({len(consolidados)} filas)")

        # Supervisión de álgebra lineal (opcional, aditiva)
        try:
            from core.algebra_lineal import supervisar_y_mejorar
            _res = supervisar_y_mejorar(csv_path=OUTPUT_TODOS, aplicar_cambios=True)
            _rs = _res.get("resumen", {}) if isinstance(_res, dict) else {}
            print("🧮 Supervisión algebra lineal: "
                  f"{_rs.get('duplicados_eliminados', 0)} duplicados, "
                  f"{_rs.get('subgeneros_rellenados', 0)} subgéneros, "
                  f"{_rs.get('tipos_lugar_rellenados', 0)} tipos de lugar")
        except Exception as e:
            print(f"⚠️ Supervisión algebra: {type(e).__name__}: {e}")
    else:
        print("🔍 Modo dry-run — sin exportar ni guardar caché.")

    # Estado y métricas
    estado = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duracion_total_s": round(time.time() - t_inicio, 2),
        "subagentes": [a["nombre"] for a in registro],
        "eventos_brutos": len(todos_eventos),
        "eventos_nuevos_añadidos": len(nuevos),
        "total_consolidado": len(consolidados),
        "modo_dry_run": dry_run,
    }
    if not dry_run:
        _guardar_json(ESTADO_FILE, estado)

    metricas_global = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duracion_total_s": round(time.time() - t_inicio, 2),
        "subagentes": [a["nombre"] for a in registro],
        "detalle": metricas_por_agente,
        "eventos_brutos": len(todos_eventos),
        "eventos_nuevos_añadidos": len(nuevos),
        "total_consolidado": len(consolidados),
        "modo_dry_run": dry_run,
    }
    _guardar_json(METRICAS_FILE, metricas_global)

    print("\n📊 Desglose por subagente (eventos nuevos añadidos):")
    for nombre, m in metricas_por_agente.items():
        print(f"   {nombre}: {m['eventos']} eventos (tiempo {m['tiempo']}s)")

    print(f"\n✅ Completado. Nuevos añadidos: {len(nuevos)} | "
          f"Total consolidado: {len(consolidados)}")
    return len(nuevos)


if __name__ == "__main__":
    ejecutar_agentes()