"""
Plugin Loader — Auto-descubre scrapers sin tocar orquestador.

Lee `scrapers/*.py` buscando funciones `scrape_*`, combina con
`config_modulos.json` (para saber cuáles están activos) y devuelve
un dict `SCRAPERS` compatible con `core/orquestador.py`.

Cómo añadir un nuevo scraper:
1. Crea `scrapers/mi_scraper.py` con una función `def scrape_mi_scraper(...)`:
    def scrape_mi_scraper(**kwargs):
        return [...]
2. Opcional: añade `scrapers/mi_scraper.json` con metadata:
    {"timeout": 120, "kwargs": {"limite": 50}, "activo": true, "tipo": "eventos"}
3. Añade la clave a `config_modulos.json` si quieres poder activarlo/desactivarlo.
4. ¡Hecho! No toques `orquestador.py` ni `_construir_SCRAPERS()`.
"""

import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRAPERS_DIR = PROJECT_ROOT / "scrapers"
CORE_DIR = PROJECT_ROOT / "core"
CONFIG_FILE = PROJECT_ROOT / "config_modulos.json"

# Tipos de módulos especiales (no en scrapers/, requieren importación custom)
MODULOS_ESPECIALES = {
    "telegram": {
        "modulo": "scrapers.telegram",
        "atributo": "scrape_telegram_events",
        "timeout": 120,
        "tipo": "eventos",
    },
    "facebook_dorks": {
        "modulo": "scrapers.facebook_dorks",
        "atributo": "scrape_facebook_dorks",
        "timeout": 120,
        "tipo": "hallazgos",  # No devuelve eventos; guarda JSON
    },
    "instagram_dorks": {
        "modulo": "scrapers.instagram_dorks",
        "atributo": "scrape_instagram_dorks",
        "timeout": 120,
        "tipo": "eventos",
    },
    "dorks_resultados": {
        "modulo": "scrapers.dorks_resultados",
        "atributo": "scrape_dorks_resultados",
        "timeout": 120,
        "tipo": "eventos",
    },
    "mcp_organizador": {
        "modulo": "core.mcp_organizador",
        "atributo": "ejecutar_mcp_dorks",
        "timeout": 120,
        "tipo": "eventos",
    },
    "enriquecer_fb_og": {
        "modulo": "scrapers.enriquecer_eventos",
        "atributo": "enriquecer_eventos",
        "timeout": 180,
        "tipo": "enriquecer",  # No crea eventos, enriquece los existentes
    },
    # --- Scrapers con firma no estándar (kwargs especiales o nombre != scrape_*) ---
    "facebook_mcp": {
        "modulo": "scrapers.facebook_mcp",
        "atributo": "scrape_facebook_events",
        "timeout": 900,  # SERP + visitas, muy pesado
        "tipo": "eventos",
        "kwargs_default": {"max_keywords": 12, "max_visitas": 20},
    },
    "resident_advisor": {
        "modulo": "scrapers.resident_advisor",
        "atributo": "scrape_ra",
        "timeout": 300,
        "tipo": "eventos",
    },
    "instagram_scraper": {
        "modulo": "scrapers.instagram_scraper",
        "atributo": "scrape_instagram_events",
        "timeout": 300,
        "tipo": "eventos",
        "kwargs_inyectar": ["deduplicador"],  # Se inyecta en runtime
    },
    "agente_coordinador": {
        "modulo": "core.agente_coordinador",
        "atributo": "ejecutar_agentes",
        "timeout": 600,
        "tipo": "eventos",
    },
    "fb_playwright_tor": {
        "modulo": "scrapers.fb_playwright_tor",
        "atributo": "scrape_fb_playwright_tor",
        "timeout": 600,  # Pesado: Playwright + visitas a FB
        "tipo": "eventos",
    },
}

# Config por defecto si no existe config_modulos.json
CONFIG_DEFECTO = {
    "activos": {},
    "proxy": {"enabled": False, "url": ""},
}


def _cargar_config_activos() -> Dict[str, bool]:
    """Lee config_modulos.json y devuelve {nombre_modulo: activo}."""
    if not CONFIG_FILE.exists():
        print(f"⚠️ {CONFIG_FILE} no encontrado — todos los scrapers activos")
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("activos", {})
    except (json.JSONDecodeError, IOError):
        return {}


def _cargar_plugin_meta(nombre_archivo: str) -> Dict[str, Any]:
    """
    Lee scrapers/<nombre>.json si existe. Devuelve metadata:
    timeout, kwargs, activo override, tipo, descripcion, etc.
    """
    meta_path = SCRAPERS_DIR / f"{nombre_archivo}.json"
    if not meta_path.exists():
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _extraer_funciones_scrape(filepath: Path) -> List[str]:
    """Devuelve nombres de funciones `def scrape_*(...)` en un archivo .py."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            contenido = f.read(8000)  # Primeras líneas suficiente
    except IOError:
        return []
    return re.findall(r"^def (scrape_\w+)\s*\(", contenido, re.MULTILINE)


def _importar_modulo(ruta_punto: str) -> Optional[Any]:
    """Importa un módulo de forma tolerante a fallos."""
    try:
        return importlib.import_module(ruta_punto)
    except ImportError:
        return None


def _construir_funcion(modulo_ruta: str, atributo: str, kwargs: Dict,
                       kwargs_inyectar: Optional[List[str]] = None) -> Callable:
    """
    Devuelve una lambda que llama a modulo.atributo(**kwargs).
    kwargs_inyectar: nombres de args que se inyectan en runtime (ej: deduplicador).
    """
    _iny = kwargs_inyectar or []

    def _ejecutar():
        mod = importlib.import_module(modulo_ruta)
        fn = getattr(mod, atributo)
        # Inyectar dependencias runtime
        call_kwargs = dict(kwargs)
        for k in _iny:
            if k == "deduplicador" and _dedup_actual_ref[0] is not None:
                call_kwargs[k] = _dedup_actual_ref[0]
        return fn(**call_kwargs) if call_kwargs else fn()
    return _ejecutar


# Referencia mutable al deduplicador actual (inyectado por orquestador)
_dedup_actual_ref: List[Any] = [None]


def inyectar_deduplicador(dedup) -> None:
    """Llamado por orquestador para pasar el deduplicador a scrapers que lo necesiten."""
    _dedup_actual_ref[0] = dedup


def descubrir_scrapers() -> Dict[str, Dict[str, Any]]:
    """
    Auto-descubre scrapers y devuelve dict compatible con SCRAPERS.

    Estructura devuelta:
    {
        "nombre_scraper": {
            "funcion": <callable>,
            "timeout": <int>,
            "activo": <bool>,
            "tipo": "eventos" | "hallazgos" | "enriquecer",
        },
        ...
    }
    """
    config_activos = _cargar_config_activos()
    scrapers: Dict[str, Dict[str, Any]] = {}

    # --- 1. Scrapers estándar: scrapers/*.py con scrape_* ---
    if SCRAPERS_DIR.exists():
        for filepath in sorted(SCRAPERS_DIR.glob("*.py")):
            if filepath.name.startswith("_"):
                continue
            nombre_modulo = filepath.stem  # ej: "setline"

            # Ya procesado como módulo especial
            if nombre_modulo in MODULOS_ESPECIALES:
                continue

            # Buscar funciones scrape_*
            funcs = _extraer_funciones_scrape(filepath)
            if not funcs:
                continue  # No es un scraper (es helper, util, etc.)

            # Tomar la primera función scrape_ encontrada
            func_name = funcs[0]

            # Metadata del plugin (timeout, kwargs, etc.)
            meta = _cargar_plugin_meta(nombre_modulo)
            timeout = meta.get("timeout", 120)
            kwargs = meta.get("kwargs", {})
            tipo = meta.get("tipo", "eventos")

            # ¿Está activo? Prioridad: meta.json → config_modulos.json → True
            if "activo" in meta:
                activo = meta["activo"]
            elif nombre_modulo in config_activos:
                activo = config_activos[nombre_modulo]
            else:
                activo = True  # Por defecto activo si no hay config

            # Construir callable lazy
            modulo_ruta = f"scrapers.{nombre_modulo}"
            funcion = _construir_funcion(modulo_ruta, func_name, kwargs)

            scrapers[nombre_modulo] = {
                "funcion": funcion,
                "timeout": timeout,
                "activo": activo,
                "tipo": tipo,
            }

    # --- 2. Módulos especiales (no estándar) ---
    for nombre, cfg in MODULOS_ESPECIALES.items():
        # ¿Está activo?
        if nombre in config_activos:
            activo = config_activos[nombre]
        else:
            activo = True

        modulo_ruta = cfg["modulo"]
        atributo = cfg["atributo"]
        timeout = cfg.get("timeout", 120)
        tipo = cfg.get("tipo", "eventos")
        kwargs_default = cfg.get("kwargs_default", {})
        kwargs_inyectar = cfg.get("kwargs_inyectar", [])

        funcion = _construir_funcion(modulo_ruta, atributo, kwargs_default,
                                     kwargs_inyectar)

        scrapers[nombre] = {
            "funcion": funcion,
            "timeout": timeout,
            "activo": activo,
            "tipo": tipo,
        }

    return scrapers


def scrapers_activos() -> Dict[str, Dict[str, Any]]:
    """Devuelve solo los scrapers con activo=True."""
    todos = descubrir_scrapers()
    return {k: v for k, v in todos.items() if v.get("activo", True)}


def obtener_scraper(nombre: str) -> Optional[Dict[str, Any]]:
    """Devuelve un scraper por nombre, o None si no existe."""
    todos = descubrir_scrapers()
    return todos.get(nombre)


def listar_scrapers() -> List[Tuple[str, bool, str]]:
    """
    Devuelve lista de (nombre, activo, tipo) para todos los scrapers descubiertos.
    Útil para debugging y UI.
    """
    todos = descubrir_scrapers()
    return [
        (nombre, cfg.get("activo", True), cfg.get("tipo", "eventos"))
        for nombre, cfg in sorted(todos.items())
    ]


def guardar_plugin_meta(nombre: str, meta: Dict[str, Any]) -> None:
    """
    Guarda metadata de un plugin en scrapers/<nombre>.json.
    Útil para auto-optimización: el loop_mejora puede guardar
    la mejor config por scraper.
    """
    meta_path = SCRAPERS_DIR / f"{nombre}.json"
    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"⚠️ No se pudo guardar meta para {nombre}: {e}")


def resumen_plugins() -> str:
    """Devuelve un string con el resumen de todos los plugins."""
    todos = descubrir_scrapers()
    activos = [k for k, v in todos.items() if v.get("activo", True)]
    inactivos = [k for k, v in todos.items() if not v.get("activo", True)]
    lineas = [f"📋 Plugins: {len(todos)} total, {len(activos)} activos"]
    if activos:
        lineas.append(f"  ✅ Activos: {', '.join(activos)}")
    if inactivos:
        lineas.append(f"  ⏸️  Inactivos: {', '.join(inactivos)}")
    return "\n".join(lineas)


# --- Para testing ---
if __name__ == "__main__":
    print(resumen_plugins())
    print()
    todos = descubrir_scrapers()
    for nombre, cfg in sorted(todos.items()):
        tipo = cfg.get("tipo", "?")
        activo = "✅" if cfg.get("activo", True) else "❌"
        timeout = cfg.get("timeout", "?")
        print(f"  {activo} {nombre:25} tipo={tipo:12} timeout={timeout}s")
