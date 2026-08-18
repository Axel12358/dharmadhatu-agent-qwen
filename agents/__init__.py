#!/usr/bin/env python3
"""
Subagentes del Sistema Multiagente Ligero (Dharmadhatu Bot v5).

Cada subagente es un wrapper fino y tolerante a fallos sobre un scraper o
módulo existente (NO se modifican los scrapers). Devuelve SIEMPRE un dict:

    {"eventos": [List[Dict]], "detalle": {Dict}}

- `eventos`: lista de eventos crudos recolectados (vacía si no hay).
- `detalle`: métricas/información del subagente (errores incluidos).

Principio: siempre sumar, nunca restar. Si un subagente falla, devuelve
{"eventos": [], "detalle": {"error": ...}} y el Coordinador continúa.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

OUTPUT_TODOS = str(Path(PROJECT_ROOT) / "eventos_encontrados.csv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ok(eventos: List[Dict], detalle: Optional[Dict] = None) -> Dict:
    return {"eventos": list(eventos or []), "detalle": dict(detalle or {})}


def _err(error: Exception) -> Dict:
    return {"eventos": [], "detalle": {"error": f"{type(error).__name__}: {error}"}}


def _leer_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Subagentes de red (scrapers existentes, integrados sin modificar)
# ---------------------------------------------------------------------------
def facebook_events_agent(max_keywords: int = 12, max_visitas: int = 20) -> Dict:
    """Facebook: búsqueda pública MCP + pipeline de grupos."""
    eventos: List[Dict] = []
    detalle: Dict[str, Any] = {}
    try:
        from scrapers.facebook_mcp import scrape_facebook_events
        t = time.time()
        evs = scrape_facebook_events(max_keywords=max_keywords,
                                     max_visitas=max_visitas) or []
        eventos.extend(evs)
        detalle["facebook_mcp"] = len(evs)
        detalle["facebook_mcp_tiempo_s"] = round(time.time() - t, 1)
    except Exception as e:
        detalle["facebook_mcp_error"] = f"{type(e).__name__}: {e}"
    try:
        from scrapers.facebook_events_from_groups import run_pipeline
        t = time.time()
        evs = run_pipeline() or []
        eventos.extend(evs)
        detalle["facebook_groups"] = len(evs)
        detalle["facebook_groups_tiempo_s"] = round(time.time() - t, 1)
    except Exception as e:
        detalle["facebook_groups_error"] = f"{type(e).__name__}: {e}"
    return _ok(eventos, detalle)


def facebook_dorks_agent(timeout: int = 180) -> Dict:
    """Facebook Dorks (OSINT vía DuckDuckGo)."""
    try:
        from scrapers.facebook_dorks import scrape_facebook_dorks
        t = time.time()
        res = scrape_facebook_dorks(timeout=timeout) or {}
        eventos = list(res.get("eventos", []) or [])
        detalle = {
            "total_hallazgos": res.get("total_hallazgos", 0),
            "grupos": res.get("grupos", 0),
            "paginas": res.get("paginas", 0),
            "correos": res.get("correos", 0),
            "tiempo_s": round(time.time() - t, 1),
        }
        return _ok(eventos, detalle)
    except Exception as e:
        return _err(e)


def instagram_dorks_agent(timeout: int = 180, limite: Optional[int] = None) -> Dict:
    """Instagram Dorks (posts + fase de perfiles)."""
    try:
        from scrapers.instagram_dorks import scrape_instagram_dorks
        t = time.time()
        evs = scrape_instagram_dorks(timeout=timeout, limite=limite, rondas=2) or []
        detalle = {
            "posts": len(evs),
            "tiempo_s": round(time.time() - t, 1),
        }
        return _ok(evs, detalle)
    except Exception as e:
        return _err(e)


def telegram_agent(timeout: int = 180) -> Dict:
    """Telegram (canales públicos, sin API)."""
    try:
        from scrapers.telegram import scrape_telegram_events
        t = time.time()
        evs = scrape_telegram_events(timeout=timeout) or []
        return _ok(evs, {"canales": len(evs), "tiempo_s": round(time.time() - t, 1)})
    except Exception as e:
        return _err(e)


def resident_advisor_agent(max_eventos: Optional[int] = 100) -> Dict:
    """Resident Advisor (GraphQL, sin login)."""
    try:
        from scrapers.resident_advisor import scrape_ra
        t = time.time()
        evs = scrape_ra(max_eventos=max_eventos) or []
        return _ok(evs, {"ciudades": "todas", "tiempo_s": round(time.time() - t, 1)})
    except Exception as e:
        return _err(e)


def goabase_agent(limit: int = 100) -> Dict:
    """Goabase API (psytrance)."""
    try:
        from scrapers.goabase import scrape_goabase
        t = time.time()
        evs = scrape_goabase(limit=limit) or []
        return _ok(evs, {"limite": limit, "tiempo_s": round(time.time() - t, 1)})
    except Exception as e:
        return _err(e)


def songkick_agent(limit: int = 100) -> Dict:
    """Songkick."""
    try:
        from scrapers.songkick import scrape_songkick
        t = time.time()
        evs = scrape_songkick(limit=limit) or []
        return _ok(evs, {"limite": limit, "tiempo_s": round(time.time() - t, 1)})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Subagentes de CPU / postproceso (no devuelven eventos)
# ---------------------------------------------------------------------------
def completador_agent(limit: Optional[int] = None) -> Dict:
    """Completa campos N/A de eventos Facebook (Playwright + búsqueda externa)."""
    detalle: Dict[str, Any] = {}
    try:
        from scrapers.completar_fb_na import completar_eventos_fb
        res = completar_eventos_fb(limit=limit)
        detalle["fb_na_actualizados"] = res.get("actualizados", 0)
        detalle["fb_na_visitados"] = res.get("visitados", res.get("procesados", 0))
    except Exception as e:
        detalle["fb_na_error"] = f"{type(e).__name__}: {e}"
    try:
        from scrapers.completar_fb_externo import completar_fb_externo
        res = completar_fb_externo(limit=limit)
        detalle["fb_externo_actualizados"] = res.get("actualizados", 0)
        detalle["fb_externo_visitados"] = res.get("visitados", res.get("procesados", 0))
    except Exception as e:
        detalle["fb_externo_error"] = f"{type(e).__name__}: {e}"
    return _ok([], detalle)


def clasificador_agent() -> Dict:
    """Supervisión/mejora por álgebra lineal (rellena N/A, dedup interno)."""
    try:
        from core.algebra_lineal import supervisar_y_mejorar
        res = supervisar_y_mejorar(csv_path=OUTPUT_TODOS, aplicar_cambios=True)
        resumen = res.get("resumen", {}) if isinstance(res, dict) else {}
        return _ok([], {
            "subgeneros_rellenados": resumen.get("subgeneros_rellenados", 0),
            "tipos_lugar_rellenados": resumen.get("tipos_lugar_rellenados", 0),
            "duplicados_eliminados": resumen.get("duplicados_eliminados", 0),
            "eventos_analizados": resumen.get("eventos_analizados", 0),
        })
    except Exception as e:
        return _err(e)


def organizador_agent() -> Dict:
    """Gestiona colas de organizadores/perfiles/correos (consolidación)."""
    detalle: Dict[str, Any] = {}
    consolidado: Dict[str, Any] = {}
    archivos = ["organizadores_facebook.json", "correos_organizadores.json",
                "grupos_encontrados.json", "eventos_facebook_dorks.json"]
    try:
        for nombre in archivos:
            path = str(Path(PROJECT_ROOT) / nombre)
            data = _leer_json(path)
            if data is None:
                detalle[nombre] = "no_disponible"
                continue
            consolidado[nombre] = data
            if isinstance(data, dict):
                # Si es {grupos:[...], total:n}, contar de forma genérica
                if "groups" in data:
                    detalle[nombre] = len(data["groups"])
                else:
                    detalle[nombre] = len(data)
            elif isinstance(data, list):
                detalle[nombre] = len(data)
        out_path = Path(PROJECT_ROOT) / "organizadores_consolidados.json"
        tmp = out_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(consolidado, f, indent=2, ensure_ascii=False)
        tmp.replace(out_path)
        detalle["consolidado"] = str(out_path)
        detalle["timestamp"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        detalle["error"] = f"{type(e).__name__}: {e}"
    return _ok([], detalle)


# ---------------------------------------------------------------------------
# Registro de subagentes disponibles (nombre, función, timeout, prioridad,
# usa_tor: si comparte el semáforo global de Tor)
# ---------------------------------------------------------------------------
def construir_registro() -> List[Dict]:
    """Lista de subagentes con su configuración por defecto."""
    return [
        {"nombre": "facebook_events_agent", "funcion": facebook_events_agent,
         "timeout": 300, "prioridad": 1, "usa_tor": True},
        {"nombre": "facebook_dorks_agent", "funcion": facebook_dorks_agent,
         "timeout": 180, "prioridad": 2, "usa_tor": True},
        {"nombre": "instagram_dorks_agent", "funcion": instagram_dorks_agent,
         "timeout": 180, "prioridad": 3, "usa_tor": True},
        {"nombre": "telegram_agent", "funcion": telegram_agent,
         "timeout": 180, "prioridad": 4, "usa_tor": True},
        {"nombre": "resident_advisor_agent", "funcion": resident_advisor_agent,
         "timeout": 300, "prioridad": 5, "usa_tor": False},
        {"nombre": "goabase_agent", "funcion": goabase_agent,
         "timeout": 120, "prioridad": 6, "usa_tor": False},
        {"nombre": "songkick_agent", "funcion": songkick_agent,
         "timeout": 120, "prioridad": 7, "usa_tor": False},
        {"nombre": "completador_agent", "funcion": completador_agent,
         "timeout": 600, "prioridad": 8, "usa_tor": True},
        {"nombre": "clasificador_agent", "funcion": clasificador_agent,
         "timeout": 120, "prioridad": 9, "usa_tor": False},
        {"nombre": "organizador_agent", "funcion": organizador_agent,
         "timeout": 60, "prioridad": 10, "usa_tor": False},
    ]


if __name__ == "__main__":
    for ag in construir_registro():
        print(f"  {ag['nombre']}: timeout={ag['timeout']}s "
              f"prioridad={ag['prioridad']} tor={ag['usa_tor']}")
