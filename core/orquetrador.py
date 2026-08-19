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
-

Filtrado por config_modulos.json: solo ejecuta los scrapers marcados como activos.
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
METRICAS_FILE = str(Path(PROJECT_ROOT) / "metricas_orquetador.json")

# Timeout por defecto para scrapers sin timeout específico
TIMEOUT_DEFECTO = 300  # segundos

# Cargar configuraci\\u00f3n de m\\u00f3dulos
CONFIG_MOD: dict = {}
config_path = os.path.join(PROJECT_ROOT, "config_modulos.json")
if os.path.exists(config_path):
    try:
        with open(config_path) as _f:
            CONFIG_MOD = json.load(_f).get("activos", {})
    except Exception:
        CONFIG_MOD = {}

# Deduplicador "actual" del run y timeout, inyectados a scrapers que lo aceptan
# (p.ej. instagram). Se fija en orquestar_scrapers().
_dedup_actual: Optional[Deduplicador] = None


def _importar(nombre_modulo: str, atributo: str) -> Callable:
    """Importaci\\u00f3n diferida y tolerante a fallos."""
    import importlib
    mod = importlib.import_module(nombre_modulo)
    return getattr(mod, atributo)


def _filtrar_scrapers_por_config(scraper_dict: dict) -> dict:
    """Filtra el diccionario de scrapers según config_modulos.json.

    Solo ejecuta los scrapers marcados como True en CONFIG_MOD.
    Los scrapers críticos (goabase, songkick) siempre se incluyen.
    """
    if not CONFIG_MOD:
        return scraper_dict
    scrapers_criticos = {"goabase", "songkick"}
    # Mapeo de nombre en config → nombre en scraper_dict
    config_to_scraper = {
        "facebook_mcp": "facebook",
        "facebook_events_from_groups": "facebook",
    }
    resultado = {}
    for nombre, cfg in scraper_dict.items():
        if nombre in scrapers_criticos:
            resultado[nombre] = cfg
        else:
            # Buscar si algún flag de config activa este scraper
            for config_key, scraper_key in config_to_scraper.items():
                if scraper_key == nombre and CONFIG_MOD.get(config_key, False):
                    resultado[nombre] = cfg
                    break
            else:
                # Direct match
                if CONFIG_MOD.get(nombre, False):
                    resultado[nombre] = cfg
    # Asegurar que haya siempre al menos goabase y songkick
    for critico in scrapers_criticos:
        if critico not in resultado and critico in scraper_dict:
            resultado[critico] = scraper_dict[critico]
    return resultado


def _construir_SCRAPERS() -> dict:
    """Construye el diccionario de scrapers activos con su timeout."""
    return {
        "goabase": {
            "funcion": lambda: _importar("scrapers.goabase", "scrape_goabase")(limit=20),
            "timeout": 30,
        },
        "songkick": {
            "funcion": lambda: _importar("scrapers.songkick", "scrape_songkick")(limit=100),
            "timeout": 60,
        },
        "resident_advisor": {
            "funcion": lambda: _importar("scrapers.resident_advisor", "scrape_ra")(),
            "timeout": 300,
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


SCRAPERS: dict = _construir_SCRAPERS()


def _ejecutar_scraper(cfg: dict) -> list:
    """Ejecuta un scraper. Soporta s\\u00edncronos y asíncronos."""
    fn = cfg["funcion"]
    result = fn()
    if asyncio.iscoroutine(result):
        return list(asyncio.run(result))
    if isinstance(result, (list, tuple)):
        return [dict(x) for x in result]
    return []


def _normalizar_evento(ev: dict) -> dict:
    """Asegura campos est\\u00e9ndar y `link` a partir de `url` si hace falta."""
    ev = dict(ev)
    if not ev.get("link") and ev.get("url"):
        ev["link"] = ev["url"]
    ev.setdefault("fuente", "Desconocida")
    return ev


def _leer_csv(filename: str) -> list:
    if not Path(filename).exists():
        return []
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except (IOError, csv.Error):
        return []


def _escribir_csv(filename: str, eventos: list) -> None:
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
    print(f"\\u2705 CSV actualizado: {filename} ({len(eventos)} filas)")


def _guardar_metricas(entry: dict) -> None:
    historial: list = []
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


def orquestar_scrapers(activos: Optional[list] = None,
                       dry_run: bool = False) -> list:
    """devuelve los eventos NUEVOS añadidos al CSV (aditivo, no duplica).

    - ejecuta los scrapers activos (por defecto todos) en paralelo con timeout.
    - recolecta, dedup global y actualiza eventos_encontrados.csv aditivamente.
    - registra metricas_orquetador.json.
    - todos los scrapers y secciones adicionales respetan config_modulos.json.
    """
    scraper_dict = _filtrar_scrapers_por_config(dict(SCRAPERS))
    if activos:
        scraper_dict = {k: v for k, v in scraper_dict.items() if k in activos}

    print("=" * 60)
    print("\\u2605 Loop Central de Optimizaci\\u00f3n \\u2014 Orquestador")
    print("\\u2605 " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
    print("=" * 60)
    print("Scrapers activos: " + ", ".join(scraper_dict.keys()))

    t_inicio = time.time()
    dedup = Deduplicador()
    global _dedup_actual
    _dedup_actual = dedup

    # Asegurar que los eventos ya consolidados en el CSV no se re-suman.
    # Registramos sus hashes en la \\u00e1cet (aditivo, no borra lo que ya est\\u00e1).
    existentes = _leer_csv(OUTPUT_TODOS)
    dedup.registrar_vistos(existentes)

    metricas_por_scraper: dict = {}
    todos_nuevos: list = []

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
                    print(f"  \\u2705 {nombre}: {len(evs_normalizados)} eventos")
                else:
                    print(f"  \\u25a0 {nombre}: 0 eventos")
            except FutTimeout:
                entrada["error"] = "timeout"
                print(f"  \\u26a0 {nombre}: timeout ({timeout}s) -- omitido")
            except Exception as e:
                entrada["error"] = f"{type(e).__name__}: {e}"
                print(f"  \\u274c {nombre}: {entrada['error']}")
            finally:
                entrada["tiempo"] = round(time.time() - t_scraper, 2)
                metricas_por_scraper[nombre] = entrada

    print(f"\n\\u26a0 Total bruto recolectado: {len(todos_nuevos)} eventos")

    # ---- Telegram (canales públicos, sin API) --opcional y aditiva ----
    # Si scrapers/telegram existe, descubre canales via DuckDuckGo + t.me/s/,
    # scrapea mensajes y extrae eventos. Nunca rompe el flujo: ante cualquier
    # problema simplemente no añade nada. Solo si está activo en config.
    try:
        if CONFIG_MOD.get("telegram", False) or "telegram" in scraper_dict:
            from scrapers.telegram import scrape_telegram_events
            eventos_telegram = scrape_telegram_events()
            if eventos_telegram:
                eventos_telegram = [
                    _normalizar_evento(e) for e in eventos_telegram
                ]
                todos_nuevos.extend(eventos_telegram)
                print(f"  \\u2705 Telegram: {len(eventos_telegram)} eventos añadidos")
            else:
                print("  \\u25a0 Telegram: 0 eventos")
        else:
            print("  \\u25a0 Telegram desactivado en config_modulos.json -- omitido")
    except ImportError:
        pass  # M\\u00f3dulo no instalado, se sigue sin él
    except Exception as e:
        print(f"  \\u274c Telegram: {type(e).__name__}: {e}")

    # ---- Facebook Dorks (Groups/Pages/Emails) --opcional y aditiva ----
    # NO devuelve eventos; guarda grupos/páginas/correos en JSON files
    # Solo si está activo en config_modulos.json
    if CONFIG_MOD.get("facebook_dorks", False):
        try:
            from scrapers.facebook_dorks import scrape_facebook_dorks
            fb_result = scrape_facebook_dorks()
            total_hallazgos = fb_result.get("total_hallazgos", 0)
            print(f"  \\u2705 Facebook Dorks: {total_hallazgos} hallazgos "
                  f"({fb_result.get('grupos', 0)} grupos, "
                  f"{fb_result.get('paginas', 0)} páginas, "
                  f"{fb_result.get('correos', 0)} correos)")
        except Exception as e:
            print(f"  \\u274c Facebook Dorks: {type(e).__name__}: {e}")
    else:
        print("  \\u25a0 Facebook Dorks desactivado en config_modulos.json -- omitido")

    # ---- Instagram Dorks (Posts + Profiles) --opcional y aditiva ----
    if CONFIG_MOD.get("instagram_dorks", False):
        try:
            from scrapers.instagram_dorks import scrape_instagram_dorks
            eventos_ig_dorks = scrape_instagram_dorks(limite=30)
            if eventos_ig_dorks:
                eventos_ig_dorks = [_normalizar_evento(e) for e in eventos_ig_dorks]
                todos_nuevos.extend(eventos_ig_dorks)
                print(f"  \\u2705 Instagram Dorks: {len(eventos_ig_dorks)} eventos añadidos")
            else:
                print("  \\u25a0 Instagram Dorks: 0 eventos")
        except Exception as e:
            print(f"  \\u274c Instagram Dorks: {type(e).__name__}: {e}")
    else:
        print("  \\u25a0 Instagram Dorks desactivado en config_modulos.json -- omitido")

    # ---- MCP Organizador (motor central de dorks) --opcional y aditiva ----
    if CONFIG_MOD.get("mcp_organizador", False):
        try:
            from core.mcp_organizador import ejecutar_mcp_dorks
            eventos_mcp = ejecutar_mcp_dorks(limite=50)
            if eventos_mcp:
                eventos_mcp = [_normalizar_evento(e) for e in eventos_mcp]
                todos_nuevos.extend(eventos_mcp)
                print(f"  \\u2705 MCP Organizador: {len(eventos_mcp)} eventos añadidos")
            else:
                print("  \\u25a0 MCP Organizador: 0 eventos")
        except Exception as e:
            print(f"  \\u274c MCP Organizador: {type(e).__name__}: {e}")
    else:
        print("  \\u25a0 MCP Organizador desactivado en config_modulos.json -- omitido")

    # ---- Agente Coordinador (Sistema Multiagente Ligero) --opcional y aditiva ----
    # Si core/agente_coordinador.py existe, ejecuta los subagentes en paralelo
    # (m\\u00e1x 3 simult\\u00e1neos, sem\\u00e1foro Tor m\\u00e1x 2) y suma eventos nuevos al CSV.
    # Nunca rompe el flujo: si no está disponible, se sigue igual que antes.
    # Solo si está activo en config_modulos.json o si el agente_coordinador.py existe
    # y no está desactivado en config.
    try:
        from pathlib import Path as _P
        agent_exists = _P(__file__).resolve().parent.joinpath("agente_coordinador.py").exists()
        agent_activo = CONFIG_MOD.get("mcp_organizador", False) or not any(
            k for k in CONFIG_MOD if k in ('facebook_mcp', 'facebook_events_from_groups', 
                'facebook_dorks', 'instagram_dorks', 'dorks_resultados', 'mcp_organizador'))
        if agent_exists:
            from core.agente_coordinador import ejecutar_agentes
            _nuevos_coord = ejecutar_agentes(dry_run=dry_run)
            print(f"  \\u2705 Agente Coordinador: {_nuevos_coord} eventos nuevos añadidos")
    except Exception as e:
        print(f"  \\u26a0 Agente Coordinador: {type(e).__name__}: {e}")

    # Deduplicaci\\u00f3n global: solo eventos no vistos (ni en \\u00e1cet ni en CSV)
    nuevos = dedup.filtrar_nuevos(todos_nuevos)
    print(f"\\u26a0 Nuevos tras dedup global: {len(nuevos)}")

    # Consolidaci\\u00f3n aditiva: CSV existente + nuevos
    consolidados = existentes + nuevos
    print(f"\\u26a0 Total consolidado: {len(existentes)} + {len(nuevos)} = {len(consolidados)}")

    if not dry_run:
        # Registrar nuevos como vistos antes de escribir
        dedup.registrar_vistos(nuevos)
        dedup.guardar()
        _escribir_csv(OUTPUT_TODOS, consolidados)
        print("\\u26a0 Cach\\u00e9 dedup guardada.")
    else:
        print("\\u26a0 Modo dry-run -- sin exportar ni guardar caché.")

    # ---- Supervisi\\u00f3n/mejora por \\u00e1lgebra lineal (opcional, aditiva) ----
    # Si core/algebra_lineal.py existe, rellena N/A (subgénero, tipo_lugar) con
    # confianza alta y deduplica duplicados internos por similitud. Nunca resta
    # eventos válidos ni sobrescribe datos; si no está disponible, se ignora.
    if not dry_run:
        try:
            from core.algebra_lineal import supervisar_y_mejorar
            _res_alg = supervisar_y_mejorar(OUTPUT_TODOS, aplicar_cambios=True)
            _rs = _res_alg.get("resumen", {})
            print("\\u26a0 Supervisi\\u00f3n algebra lineal: "
                  f"{_rs.get('duplicados_eliminados', 0)} duplicados, "
                  f"{_rs.get('subgeneros_rellenados', 0)} subgéneros, "
                  f"{_rs.get('tipos_lugar_rellenados', 0)} tipos de lugar")
        except ImportError:
            pass  # M\\u00f3dulo no disponible, se sigue sin él
        except Exception as e:
            print(f"\\u26a0 Supervisi\\u00f3n algebra: {type(e).__name__}: {e}")

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
    print("\\n\\u26a0 Desglose por fuente (nuevos añadidos):")
    fuentes = {}
    for nombre, m in metricas_por_scraper.items():
        if m["eventos"]:
            fuentes[nombre] = m["eventos"]
    for f, c in sorted(fuentes.items(), key=lambda x: -x[1]):
        print(f"   {f}: {c}")

    print(f"\\u26a0 Completado. Nuevos añadidos: {len(nuevos)} | "
          f"Total consolidado: {len(consolidados)}")
    return nuevos


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    nuevos = orquestar_scrapers(dry_run=dry)
    print(f"\n(CLI) Eventos nuevos añadidos: {len(nuevos)}")