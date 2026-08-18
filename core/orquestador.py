#!/usr/bin/env python3
"""
Loop Central de Optimización — orquestador de scrapers (Dharmadhatu Bot v5).

Ejecuta en paralelo todos los scrapers activos definidos en SCRAPERS, cada uno
con un timeout duro. Recolecta resultados, los pasa por el Deduplicador global
y actualiza `eventos_encontrados.csv` de forma aditiva (nunca borra datos).

Patrón: Orchestrator + Workers con `concurrent.futures.ThreadPoolExecutor`.

Notas:
- Scrapers síncronos se ejecutan en un hilo del pool (timeout por future).
- Scrapers asíncronos se ejecutan dentro de un hilo con `asyncio.run`.
- Cualquier excepción o timeout se captura y NO rompe el resto.
- `orquestar_scrapers(dry_run=True)` valida sin tocar disco ni caché.
"""

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

# Directorio raíz del proyecto
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

OUTPUT_TODOS = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")
METRICAS_FILE = str(Path(PROJECT_ROOT) / "metricas_orquestador.json")

# Timeout por defecto para scrapers sin timeout específico
TIMEOUT_DEFECTO = 300  # segundos

# Deduplicador "actual" del run y timeout, inyectados a scrapers que lo aceptan
# (p.ej. instagram). Se fija en orquestar_scrapers().
_dedup_actual: Optional[Deduplicador] = None


def _importar(nombre_modulo: str, atributo: str) -> Callable:
    """Importación diferida y tolerante a fallos."""
    import importlib
    mod = importlib.import_module(nombre_modulo)
    return getattr(mod, atributo)


def _construir_SCRAPERS() -> Dict[str, Dict[str, Any]]:
    """Construye el diccionario de scrapers activos con su timeout."""
    return {
        "goabase": {
            "funcion": lambda: _importar("scrapers.goabase", "scrape_goabase")(limit=100),
            "timeout": 120,
        },
        "songkick": {
            "funcion": lambda: _importar("scrapers.songkick", "scrape_songkick")(limit=100),
            "timeout": 120,
        },
        "resident_advisor": {
            "funcion": lambda: _importar("scrapers.resident_advisor", "scrape_ra")(),
            "timeout": 300,
        },
        "facebook": {
            "funcion": lambda: _importar("scrapers.facebook_mcp", "scrape_facebook_events")(
                max_keywords=12, max_visitas=20
            ),
            "timeout": 900,  # Facebook es pesado (SERP + visitas)
        },
        "instagram": {
            "funcion": lambda: _importar("scrapers.instagram_scraper", "scrape_instagram_events")(
                timeout=TIMEOUT_DEFECTO,
                deduplicador=_dedup_actual,
            ),
            "timeout": 300,
        },
        "reddit": {
            "funcion": lambda: _importar("scrapers.reddit_psy", "scrape_reddit_psy")(),
            "timeout": 60,
        },
        "psytrance_pl": {
            "funcion": lambda: _importar("scrapers.psytrance_pl", "scrape_psytrance_pl")(),
            "timeout": 60,
        },
        "isratrance": {
            "funcion": lambda: _importar("scrapers.isratrance", "scrape_isratrance")(),
            "timeout": 60,
        },
        "ektoplazm": {
            "funcion": lambda: _importar("scrapers.ektoplazm", "scrape_ektoplazm")(),
            "timeout": 60,
        },
        "meetup": {
            "funcion": lambda: _importar("scrapers.meetup_psy", "scrape_meetup_psy")(),
            "timeout": 60,
        },
    }


SCRAPERS: Dict[str, Dict[str, Any]] = _construir_SCRAPERS()


def _ejecutar_scraper(cfg: Dict[str, Any]) -> List[Dict]:
    """Ejecuta un scraper. Soporto síncronos y asíncronos."""
    fn = cfg["funcion"]
    result = fn()
    if asyncio.iscoroutine(result):
        return list(asyncio.run(result))
    if isinstance(result, (list, tuple)):
        return [dict(x) for x in result]
    return []


def _normalizar_evento(ev: Dict) -> Dict:
    """Asegura campos estándar y `link` a partir de `url` si hace falta."""
    ev = dict(ev)
    if not ev.get("link") and ev.get("url"):
        ev["link"] = ev["url"]
    ev.setdefault("fuente", "Desconocida")
    return ev


def _leer_csv(filename: str) -> List[Dict]:
    if not Path(filename).exists():
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (IOError, csv.Error):
        return []


def _escribir_csv(filename: str, eventos: List[Dict]) -> None:
    keys = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar"]
    tmp = filename + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for ev in eventos:
            link = ev.get("link") or ev.get("url") or "N/A"
            out = dict(ev)
            out["link"] = link
            writer.writerow(out)
    os.replace(tmp, filename)
    print(f"✅ CSV actualizado: {filename} ({len(eventos)} filas)")


def _guardar_metricas(entry: Dict[str, Any]) -> None:
    historial: List[Any] = []
    if Path(METRICAS_FILE).exists():
        try:
            with open(METRICAS_FILE, "r", encoding="utf-8") as f:
                cargado = json.load(f)
                if isinstance(cargado, list):
                    historial = cargado
        except (json.JSONDecodeError, IOError):
            historial = []
    historial.append(entry)
    try:
        tmp = METRICAS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(historial, f, indent=2, ensure_ascii=False)
        os.replace(tmp, METRICAS_FILE)
    except (IOError, OSError):
        pass


def orquestar_scrapers(activos: Optional[List[str]] = None,
                       dry_run: bool = False) -> List[Dict]:
    """devuelve los eventos NUEVOS añadidos al CSV (aditivo, no duplica).

    - ejecuta los scrapers activos (por defecto todos) en paralelo con timeout.
    - recolecta, dedup global y actualiza eventos_encontrados.csv aditivamente.
    - registra metricas_orquestador.json.
    """
    scraper_dict = dict(SCRAPERS)
    if activos:
        scraper_dict = {k: v for k, v in scraper_dict.items() if k in activos}

    print("=" * 60)
    print("🧠 Loop Central de Optimización — Orquestador")
    print("📅 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)
    print("Scrapers activos: " + ", ".join(scraper_dict.keys()))

    t_inicio = time.time()
    dedup = Deduplicador()
    global _dedup_actual
    _dedup_actual = dedup

    # Asegurar que los eventos ya consolidados en el CSV no se re-suman.
    # Registramos sus hashes en la caché (aditivo, no borra lo que ya está).
    existentes = _leer_csv(OUTPUT_TODOS)
    dedup.registrar_vistos(existentes)

    metricas_por_scraper: Dict[str, Dict[str, Any]] = {}
    todos_nuevos: List[Dict] = []

    with ThreadPoolExecutor(max_workers=len(scraper_dict) or 1) as pool:
        futuros = {}
        for nombre, cfg in scraper_dict.items():
            timeout = cfg.get("timeout", TIMEOUT_DEFECTO) or TIMEOUT_DEFECTO
            futuros[nombre] = (pool.submit(_ejecutar_scraper, cfg), timeout)

        for nombre, (fut, timeout) in futuros.items():
            t_scraper = time.time()
            entrada = {
                "scraper": nombre,
                "timeout": timeout,
                "exito": False,
                "eventos": 0,
                "tiempo": 0.0,
                "error": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            try:
                evs = fut.result(timeout=timeout)
                evs_normalizados = [_normalizar_evento(e) for e in (evs or [])]
                entrada["exito"] = True
                entrada["eventos"] = len(evs_normalizados)
                todos_nuevos.extend(evs_normalizados)
                if evs_normalizados:
                    print(f"  ✅ {nombre}: {len(evs_normalizados)} eventos")
                else:
                    print(f"  ⚠️ {nombre}: 0 eventos")
            except FutTimeout:
                entrada["error"] = "timeout"
                print(f"  ⚠️ {nombre}: timeout ({timeout}s) — omitido")
            except Exception as e:
                entrada["error"] = f"{type(e).__name__}: {e}"
                print(f"  ❌ {nombre}: {entrada['error']}")
            finally:
                entrada["tiempo"] = round(time.time() - t_scraper, 2)
                metricas_por_scraper[nombre] = entrada

    print(f"\n📊 Total bruto recolectado: {len(todos_nuevos)} eventos")

    # ---- Telegram (canales públicos, sin API) — opcional y aditiva ----
    # Si scrapers/telegram existe, descubre canales via DuckDuckGo + t.me/s/,
    # scrapea mensajes y extrae eventos. Nunca rompe el flujo: ante cualquier
    # problema simplemente no añade nada.
    try:
        from scrapers.telegram import scrape_telegram_events
        eventos_telegram = scrape_telegram_events()
        if eventos_telegram:
            eventos_telegram = [
                _normalizar_evento(e) for e in eventos_telegram
            ]
            todos_nuevos.extend(eventos_telegram)
            print(f"  ✅ Telegram: {len(eventos_telegram)} eventos añadidos")
        else:
            print("  ⚠️ Telegram: 0 eventos")
    except ImportError:
        pass  # Módulo no instalado, se sigue sin él
    except Exception as e:
        print(f"  ❌ Telegram: {type(e).__name__}: {e}")

    # ---- Facebook Dorks (Groups/Pages/Emails) — opcional y aditiva ----
    # NO devuelve eventos; guarda grupos/páginas/correos en JSON files
    try:
        from scrapers.facebook_dorks import scrape_facebook_dorks
        fb_result = scrape_facebook_dorks()
        total_hallazgos = fb_result.get("total_hallazgos", 0)
        print(f"  ✅ Facebook Dorks: {total_hallazgos} hallazgos "
              f"({fb_result.get('grupos', 0)} grupos, "
              f"{fb_result.get('paginas', 0)} páginas, "
              f"{fb_result.get('correos', 0)} correos)")
    except ImportError:
        pass
    except Exception as e:
        print(f"  ❌ Facebook Dorks: {type(e).__name__}: {e}")

    # ---- Instagram Dorks (Posts + Profiles) — opcional y aditiva ----
    try:
        from scrapers.instagram_dorks import scrape_instagram_dorks
        eventos_ig_dorks = scrape_instagram_dorks(limite=30)
        if eventos_ig_dorks:
            eventos_ig_dorks = [_normalizar_evento(e) for e in eventos_ig_dorks]
            todos_nuevos.extend(eventos_ig_dorks)
            print(f"  ✅ Instagram Dorks: {len(eventos_ig_dorks)} eventos añadidos")
        else:
            print("  ⚠️ Instagram Dorks: 0 eventos")
    except ImportError:
        pass
    except Exception as e:
        print(f"  ❌ Instagram Dorks: {type(e).__name__}: {e}")

    # ---- MCP Organizador / Dorks Unificado (motor central) — opcional y aditiva ----
    try:
        from core.mcp_organizador import ejecutar_mcp_dorks
        eventos_mcp = ejecutar_mcp_dorks(limite=50)
        if eventos_mcp:
            eventos_mcp = [_normalizar_evento(e) for e in eventos_mcp]
            todos_nuevos.extend(eventos_mcp)
            print(f"  ✅ MCP Organizador: {len(eventos_mcp)} eventos añadidos")
        else:
            print("  ⚠️ MCP Organizador: 0 eventos")
    except ImportError:
        pass
    except Exception as e:
        print(f"  ❌ MCP Organizador: {type(e).__name__}: {e}")

    # ---- Agente Coordinador (Sistema Multiagente Ligero) — opcional y aditiva ----
    # Si core/agente_coordinador.py existe, ejecuta los subagentes en paralelo
    # (máx 3 simultáneos, semáforo Tor máx 2) y suma eventos nuevos al CSV.
    # Nunca rompe el flujo: si no está disponible, se sigue igual que antes.
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath("agente_coordinador.py").exists():
            from core.agente_coordinador import ejecutar_agentes
            _nuevos_coord = ejecutar_agentes(dry_run=dry_run)
            print(f"  ✅ Agente Coordinador: {_nuevos_coord} eventos nuevos añadidos")
    except ImportError:
        pass
    except Exception as e:
        print(f"  ⚠️ Agente Coordinador: {type(e).__name__}: {e}")

    # Deduplicación global: solo eventos no vistos (ni en caché ni en CSV)
    nuevos = dedup.filtrar_nuevos(todos_nuevos)
    print(f"🔄 Nuevos tras dedup global: {len(nuevos)}")

    # Consolidación aditiva: CSV existente + nuevos
    consolidados = existentes + nuevos
    print(f"📈 Total consolidado: {len(existentes)} + {len(nuevos)} = {len(consolidados)}")

    if not dry_run:
        # Registrar nuevos como vistos antes de escribir
        dedup.registrar_vistos(nuevos)
        dedup.guardar()
        _escribir_csv(OUTPUT_TODOS, consolidados)
        print("💾 Caché dedup guardada.")
    else:
        print("🔍 Modo dry-run — sin exportar ni guardar caché.")

    # ---- Supervisión/mejora por álgebra lineal (opcional, aditiva) ----
    # Si core/algebra_lineal.py existe, rellena N/A (subgénero, tipo_lugar) con
    # confianza alta y deduplica duplicados internos por similitud. Nunca resta
    # eventos válidos ni sobrescribe datos; si no está disponible, se ignora.
    if not dry_run:
        try:
            from core.algebra_lineal import supervisar_y_mejorar
            _res_alg = supervisar_y_mejorar(OUTPUT_TODOS, aplicar_cambios=True)
            _rs = _res_alg.get("resumen", {})
            print("🧮 Supervisión algebra lineal: "
                  f"{_rs.get('duplicados_eliminados', 0)} duplicados, "
                  f"{_rs.get('subgeneros_rellenados', 0)} subgéneros, "
                  f"{_rs.get('tipos_lugar_rellenados', 0)} tipos de lugar")
        except ImportError:
            pass  # Módulo no disponible, se sigue sin él
        except Exception as e:
            print(f"⚠️ Supervisión algebra: {type(e).__name__}: {e}")

    # Métricas globales
    metricas_global = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duracion_total_s": round(time.time() - t_inicio, 2),
        "scrapers": list(scraper_dict.keys()),
        "detalle": metricas_por_scraper,
        "eventos_brutos": len(todos_nuevos),
        "eventos_nuevos_añadidos": len(nuevos),
        "total_consolidado": len(consolidados),
        "modo_dry_run": dry_run,
    }
    _guardar_metricas(metricas_global)

    # Resumen por fuente
    print("\n📊 Desglose por fuente (nuevos añadidos):")
    fuentes = {}
    for nombre, m in metricas_por_scraper.items():
        if m["eventos"]:
            fuentes[nombre] = m["eventos"]
    for f, c in sorted(fuentes.items(), key=lambda x: -x[1]):
        print(f"   {f}: {c}")

    print(f"\n✅ Completado. Nuevos añadidos: {len(nuevos)} | "
          f"Total consolidado: {len(consolidados)}")
    return nuevos


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    nuevos = orquestar_scrapers(dry_run=dry)
    print(f"\n(CLI) Eventos nuevos añadidos: {len(nuevos)}")