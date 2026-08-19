#!/usr/bin/env python3
"""
Agente Coordinador — Sistema Multiagente Ligero (Dharmadhatu Bot v5).

- Carga la lista de subagentes disponibles (función, timeout, prioridad).
- Ejecuta subagentes en paralelo con `ThreadPoolExecutor` (máx 3 simultáneos).
- Los subagentes que usan Tor comparten un semáforo global (máx 2 a la vez).
- Recolecta resultados, aplica deduplicación global (core/deduplicador.py)
  y supervisión de álgebra lineal.
- Guarda métricas (`metricas_agentes.json`) y estado (`estado_agentes.json`).

Principio: siempre sumar, nunca restar. Si un subagente falla, los demás
continúan. Es idempotente: re-ejecutar no duplica eventos en el CSV.
"""

from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, List, Optional

from core.deduplicador import Deduplicador
from core.task_queue import TaskQueue

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

OUTPUT_TODOS = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")
METRICAS_FILE = str(Path(PROJECT_ROOT) / "metricas_agentes.json")
ESTADO_FILE = str(Path(PROJECT_ROOT) / "estado_agentes.json")
QUEUE_FILE = str(Path(PROJECT_ROOT) / "task_queue_state.json")

MAX_SIMULTANEOS = 2
MAX_TOR_SIMULTANEOS = 1
MAX_RETRIES_TOR = 3  # Reintentos con rotación Tor para 0 hallazgos
TIMEOUT_DEFECTO = 300

# Semáforo global: subagentes que usan Tor nunca superan MAX_TOR_SIMULTANEOS
SEMAFORO_TOR = BoundedSemaphore(MAX_TOR_SIMULTANEOS)

# CSV fieldnames estándar del bot (mismo orden que orquestador.py)
CSV_KEYS = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar"]


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
        writer = csv.DictWriter(f, fieldnames=CSV_KEYS, extrasaction="ignore")
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


# ---------------------------------------------------------------------------
# Carga de subagentes
# ---------------------------------------------------------------------------
def _cargar_subagentes(activos: Optional[List[str]] = None) -> List[Dict]:
    """Carga el registro de subagentes (opcionalmente filtrado por nombre)."""
    from agents import construir_registro
    registro = construir_registro()
    if activos:
        registro = [ag for ag in registro if ag["nombre"] in activos]
    return registro


def _ejecutar_subagente(ag: Dict) -> Dict:
    """Ejecuta un subagente respetando el semáforo global de Tor."""
    fn: Callable[[], Dict] = ag["funcion"]
    if ag.get("usa_tor"):
        with SEMAFORO_TOR:
            return fn()
    return fn()


# ---------------------------------------------------------------------------
# Coordinador
# ---------------------------------------------------------------------------
def ejecutar_agentes(activos: Optional[List[str]] = None,
                     max_simultaneos: int = MAX_SIMULTANEOS,
                     dry_run: bool = False) -> int:
    """Ejecuta los subagentes en paralelo y devuelve el nº de eventos NUEVOS
    añadidos al CSV (aditivo, no duplica; 0 si ya estaban todos vistos).

    - `activos`: subconjunto de subagentes a ejecutar (None = todos).
    - `max_simultaneos`: nº máximo de subagentes concurrentes.
    - `dry_run`: no escribe CSV ni caché (solo valida el flujo).
    """
    registro = _cargar_subagentes(activos)
    if not registro:
        print("⚠️ No hay subagentes configurados.")
        return 0

    print("=" * 60)
    print("🤖 Agente Coordinador — Sistema Multiagente Ligero")
    print("📅 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)
    print(f"Subagentes ({len(registro)}): " + ", ".join(a["nombre"] for a in registro))
    print(f"Paralelismo máx: {max_simultaneos} | Tor simultáneo máx: {MAX_TOR_SIMULTANEOS}")
    print(f"  Nota: máximo 2 subagentes con Tor a la vez (semáforo global)")

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
            futuros[ag["nombre"]] = (pool.submit(_ejecutar_subagente, ag),
                                     ag.get("timeout", TIMEOUT_DEFECTO),
                                     time.time())

        for nombre, (fut, timeout, t_submit) in futuros.items():
            t_ag = t_submit
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
            try:
                # Buscar el subagente en el registro para determinar si usa Tor
                ag = next((a for a in registro if a["nombre"] == nombre), {})
                resultado = fut.result(timeout=timeout)
                evs = [dict(e) for e in (resultado or {}).get("eventos", []) or []]
                evs_normalizados = [_normalizar_evento(e) for e in evs]
                entrada["exito"] = True
                entrada["eventos"] = len(evs_normalizados)
                entrada["detalle"] = dict(resultado.get("detalle", {}) or {})

                # --- Detección de 0 hallazgos + reintento con rotación Tor ---
                if not evs_normalizados and ag.get("usa_tor"):
                    for retry in range(1, MAX_RETRIES_TOR + 1):
                        print(f"  ⚠️ {nombre}: 0 hallazgos — reintentando "
                              f"intento {retry}/{MAX_RETRIES_TOR} con Tor rotado...")
                        # Rotar Tor
                        try:
                            from core.tor_utils import rotar_tor
                            rotado = rotar_tor()
                            print(f"  🔁 Tor rotado: {rotado}")
                        except Exception as e_rot:
                            print(f"  ⚠️ Rotación Tor falló: {str(e_rot)[:60]}")
                        # Pausa breve entre rotación y reintento
                        time.sleep(5)
                        # Re-ejecutar el subagente (dentro del semáforo Tor si aplica)
                        try:
                            with SEMAFORO_TOR:
                                resultado = _ejecutar_subagente(ag)
                            evs = [dict(e) for e in (resultado or {}).get("eventos", []) or []]
                            evs_normalizados = [_normalizar_evento(e) for e in evs]
                            entrada["eventos"] = len(evs_normalizados)
                            entrada["detalle"] = dict(resultado.get("detalle", {}) or {})
                            if evs_normalizados:
                                print(f"  ✅ {nombre}: ÉXITO en retry {retry} "
                                      f"({len(evs_normalizados)} eventos)")
                                break
                        except Exception as e_retry:
                            print(f"  ❌ {nombre} retry {retry}: {str(e_retry)[:60]}")

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

    consolidados = existentes + nuevos
    print(f"📈 Total consolidado: {len(existentes)} + {len(nuevos)} = {len(consolidados)}")

    if not dry_run:
        dedup.registrar_vistos(nuevos)
        dedup.guardar()
        _escribir_csv(OUTPUT_TODOS, consolidados)
        print(f"💾 CSV actualizado: {OUTPUT_TODOS} ({len(consolidados)} filas)")

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
        "cola": cola.resumen(),
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
