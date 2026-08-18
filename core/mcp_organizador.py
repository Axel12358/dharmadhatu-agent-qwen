#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Organizador — Motor central de búsquedas con dorks para el Dharmadhatu Bot.

Centraliza la lógica de consultas dorks para Facebook, Instagram y otras fuentes.
Ejecuta búsquedas de forma SECUENCIAL (dorks_unificado → facebook_dorks →
instagram_dorks, cada una con su presupuesto) para no saturar Tor, deduplica
y consolida eventos en un repositorio local JSON. Diseñado para integrarse
con el orquestador existente.

Opcionalmente expone una interfaz JSON-RPC sobre stdio para que agentes
externos (como OpenCode) puedan consultar el organizador.

Uso como módulo:
    from core.mcp_organizador import MCPOrganizador
    org = MCPOrganizador()
    eventos = org.ejecutar_busquedas(limite=20)

Uso como CLI:
    python3 core/mcp_organizador.py                # Ejecutar búsquedas
    python3 core/mcp_organizador.py --json-rpc     # Modo JSON-RPC (stdio)
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Tor integration (optional) ---
try:
    from core.tor_manager import tor_disponible as _tor_ok
except ImportError:
    _tor_ok = lambda: False

# --- SearXNG availability check ---
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
_searxng_available = None

def _check_searxng_available() -> bool:
    global _searxng_available
    if _searxng_available is not None:
        return _searxng_available
    try:
        import requests
        r = requests.get(f"{SEARXNG_URL}/search", params={"q": "test", "format": "json"}, timeout=5)
        _searxng_available = r.status_code == 200
    except Exception:
        _searxng_available = False
    return _searxng_available

# --- State files ---
_ESTADO_FILE = Path(_PROJECT_ROOT) / "mcp_organizador_estado.json"
_REPO_EVENTOS = Path(_PROJECT_ROOT) / "mcp_eventos.json"
_RENDIMIENTO_FILE = Path(_PROJECT_ROOT) / "mcp_rendimiento.json"

# --- Constants ---
TIMEOUT_DEFAULT = 300  # 5 minutes total
MAX_WORKERS = 3  # Parallel dork search workers
MAX_EVENTS_REPO = 2000  # Max events in local repo

# Engine cascade (SearXNG first if available, then cascada manual)
ENGINE_CASCADE = ["searxng", "google_playwright", "startpage", "mojeek", "ddg", "bing"]
ENGINE_CASCADE_TOR = ["searxng", "google_playwright", "startpage", "mojeek", "ddg", "bing"]


class MCPOrganizador:
    """Motor central de búsquedas con dorks.

    Registra fuentes de dorks, ejecuta búsquedas de forma SECUENCIAL
    (evitando competencia por Tor), deduplica y consolida eventos en un
    repositorio local.
    """

    def __init__(self, fuentes_config: Optional[Dict] = None):
        """Inicializa el organizador.

        Args:
            fuentes_config: Configuración opcional de fuentes.
                Si es None, carga la configuración por defecto.
        """
        self._fuentes: Dict[str, Dict[str, Any]] = {}
        self._estado: Dict[str, Any] = {}
        self._dedup = None
        self._motores_disponibles: List[str] = []

        # Load existing state
        self.cargar_estado()

        # Determine available engines
        self._actualizar_motores_disponibles()

        # Register default sources
        self._registrar_fuentes_por_defecto()

        # Override with custom config if provided
        if fuentes_config:
            for nombre, cfg in fuentes_config.items():
                self.registrar_fuente(
                    nombre,
                    dorks=cfg.get("dorks", []),
                    motores=cfg.get("motores", self._motores_disponibles),
                    timeout=cfg.get("timeout", 120),
                    habilitado=cfg.get("habilitado", True),
                )

    def _actualizar_motores_disponibles(self) -> None:
        """Actualiza la lista de motores disponibles según SearXNG y Playwright."""
        motores = []
        # SearXNG first if available
        if _check_searxng_available():
            motores.append("searxng")
        # Google Playwright if Playwright installed
        try:
            import playwright
            motores.append("google_playwright")
        except ImportError:
            pass
        # Rest of cascade
        for m in ["startpage", "mojeek", "ddg", "bing"]:
            motores.append(m)
        self._motores_disponibles = motores

    def _registrar_fuentes_por_defecto(self) -> None:
        """Registra las fuentes de dorks por defecto con cascade multi-motor.

        ORDEN DE EJECUCIÓN SECUENCIAL (para no saturar Tor):
        1. dorks_unificado (motor unificado dorks_resultados) → 120s
        2. facebook_dorks → 120s
        3. instagram_dorks → 120s
        Cada fuente respeta su presupuesto; si no termina, devuelve lo que
        haya recolectado. NO se lanzan en paralelo.
        """
        # Generic dorks (all platforms) - main unified search: PRIMERO
        self.registrar_fuente(
            "dorks_unificado",
            dorks_generator=self._generar_dorks_unificado,
            motores=list(self._motores_disponibles),
            timeout=120,
            habilitado=True,
        )

        # Facebook Events + Groups + Pages (via dorks_resultados): SEGUNDO
        self.registrar_fuente(
            "facebook_dorks",
            dorks_generator=self._generar_dorks_facebook,
            motores=list(self._motores_disponibles),
            timeout=120,
            habilitado=True,
        )

        # Instagram Posts + Profiles (via dorks_resultados): TERCERO
        self.registrar_fuente(
            "instagram_dorks",
            dorks_generator=self._generar_dorks_instagram,
            motores=list(self._motores_disponibles),
            timeout=120,
            habilitado=True,
        )

    # Orden de ejecución secuencial de las fuentes por defecto (1º a 3º).
    ORDEN_EJECUCION = ["dorks_unificado", "facebook_dorks", "instagram_dorks"]

    def registrar_fuente(
        self,
        nombre: str,
        dorks: Optional[List[str]] = None,
        dorks_generator: Optional[Callable[[], List[str]]] = None,
        motores: Optional[List[str]] = None,
        timeout: int = 120,
        habilitado: bool = True,
    ) -> None:
        """Registra una fuente de dorks.

        Args:
            nombre: Nombre único de la fuente.
            dorks: Lista estática de dorks. Si se provee, se usa directamente.
            dorks_generator: Función que genera dorks dinámicamente.
                Se usa si `dorks` es None.
            motores: Lista de motores de búsqueda. Default: self._motores_disponibles.
            timeout: Timeout para esta fuente en segundos.
            habilitado: Si False, se salta durante la ejecución.
        """
        self._fuentes[nombre] = {
            "dorks": dorks or [],
            "dorks_generator": dorks_generator,
            "motores": motores or list(self._motores_disponibles),
            "timeout": timeout,
            "habilitado": habilitado,
            "ultima_ejecucion": None,
            "total_eventos": 0,
            "total_ejecuciones": 0,
            "ultimos_metricas": {},
        }

    def ejecutar_busquedas(
        self,
        limite: int = 30,
        timeout_total: int = TIMEOUT_DEFAULT,
    ) -> List[Dict]:
        """Ejecuta búsquedas de dorks para todas las fuentes registradas.

        SECUENCIAL: las fuentes se ejecutan una tras otra en orden
        (dorks_unificado → facebook_dorks → instagram_dorks), cada una con su
        propio presupuesto de tiempo. NO se lanzan en paralelo: en paralelo
        compiten por Tor (3 fuentes a la vez → Tor saturado → 0 resultados).
        Si una fuente no termina dentro de su presupuesto, devuelve lo que
        haya recolectado.

        Args:
            limite: Máximo de eventos a devolver.
            timeout_total: Timeout global en segundos.

        Returns:
            Lista de eventos deduplicados.
        """
        fin = time.time() + timeout_total
        tor_status = "Tor ✓" if _tor_ok() else "directo"
        searxng_status = "SearXNG ✓" if _check_searxng_available() else "SearXNG ✗"
        motores_str = " → ".join(self._motores_disponibles)
        print("=" * 60)
        print(f"🔧 MCP Organizador — Búsquedas con Dorks [{tor_status}, {searxng_status}]")
        print(f"   Motores: {motores_str}")
        print("=" * 60)

        # Get or create deduplicator
        try:
            from core.deduplicador import Deduplicador
            if self._dedup is None:
                self._dedup = Deduplicador()
                # Register existing events from repo
                existentes = self._cargar_repo()
                self._dedup.registrar_vistos(existentes)
        except ImportError:
            self._dedup = None

        # Collect all dorks from all sources
        tareas: Dict[str, Dict[str, Any]] = {}
        for nombre, fuente in self._fuentes.items():
            if not fuente.get("habilitado", True):
                continue

            # Get dorks
            dorks = fuente.get("dorks", [])
            gen = fuente.get("dorks_generator")
            if not dorks and gen:
                try:
                    dorks = gen()
                except Exception as e:
                    print(f"  ⚠️ {nombre}: error generando dorks: {e}")
                    continue
            if not dorks:
                continue

            # Calculate per-source timeout
            tiempo_restante = max(30, fin - time.time())
            timeout_fuente = min(fuente.get("timeout", 120), tiempo_restante)

            tareas[nombre] = {
                "dorks": dorks,
                "motores": fuente.get("motores", list(self._motores_disponibles)),
                "timeout": timeout_fuente,
            }

        if not tareas:
            print("  ⚠️ Sin fuentes o dorks para procesar")
            return []

        print(f"  📋 {len(tareas)} fuentes (SECUENCIAL), timeout global: {timeout_total}s")

        # Execute SECUENCIALMENTE (dorks_unificado → facebook_dorks → instagram_dorks)
        todos_eventos: List[Dict] = []
        metricas_globales: Dict[str, Dict] = {}

        orden = getattr(self, "ORDEN_EJECUCION", None) or list(self._fuentes.keys())
        for nombre in orden:
            if nombre not in tareas:
                continue
            if time.time() > fin:
                print(f"  ⏰ MCP Organizador: timeout global alcanzado, "
                      f"omitimos {nombre}")
                break

            tarea = tareas[nombre]
            print(f"  ▶️  {nombre} (presupuesto {tarea['timeout']}s)...")
            try:
                resultado = self._ejecutar_fuente(
                    nombre,
                    tarea["dorks"],
                    tarea["motores"],
                    tarea["timeout"],
                )
                if isinstance(resultado, dict):
                    eventos = resultado.get("eventos", [])
                    metricas = resultado.get("metricas", {})
                else:
                    # Backward compat: old sources return list
                    eventos = resultado
                    metricas = {}

                if eventos:
                    todos_eventos.extend(eventos)
                    print(f"  ✅ {nombre}: {len(eventos)} eventos")
                else:
                    print(f"  ⚠️ {nombre}: 0 eventos")

                metricas_globales[nombre] = metricas

            except Exception as e:
                print(f"  ❌ {nombre}: {type(e).__name__}: {e}")

        # Deduplicate
        if self._dedup is not None:
            nuevos = self._dedup.filtrar_nuevos(todos_eventos)
        else:
            # Simple URL-based dedup
            vistos: Set[str] = set()
            nuevos = []
            for ev in todos_eventos:
                url = ev.get("link") or ev.get("url") or ""
                if url and url not in vistos:
                    vistos.add(url)
                    nuevos.append(ev)

        # Apply limite
        eventos_finales = nuevos[:limite]

        # Update state
        self._estado["ultima_ejecucion"] = datetime.now(timezone.utc).isoformat()
        self._estado["total_eventos"] = self._estado.get("total_eventos", 0) + len(eventos_finales)
        self._estado["total_ejecuciones"] = self._estado.get("total_ejecuciones", 0) + 1
        self._estado["metricas_ultima_ejecucion"] = metricas_globales

        # Save dedup cache
        if self._dedup is not None:
            try:
                self._dedup.registrar_vistos(eventos_finales)
                self._dedup.guardar()
            except Exception:
                pass

        # Save to repo
        self._agregar_al_repo(eventos_finales)
        self.guardar_estado()

        print(f"\n📊 MCP Organizador: {len(eventos_finales)} eventos nuevos "
              f"(de {len(todos_eventos)} brutos)")
        print("=" * 60)

        return eventos_finales

    def _ejecutar_fuente(
        self,
        nombre: str,
        dorks: List[str],
        motores: List[str],
        timeout: int,
    ) -> Dict:
        """Ejecuta la búsqueda de dorks para una fuente específica.
        Usa Tor si está disponible, cascade multi-motor con per-engine timeout.

        Returns:
            Dict con "eventos" (List[Dict]) y "metricas" (Dict)
        """
        if not motores:
            active_cascade = ENGINE_CASCADE_TOR if _tor_ok() else ENGINE_CASCADE
            motores = list(active_cascade)

        t0 = time.time()
        metricas = {
            "dorks_procesados": 0,
            "eventos_encontrados": 0,
            "tiempo_s": 0,
            "motores_usados": motores,
        }

        try:
            if nombre == "facebook_dorks":
                from scrapers.facebook_dorks import scrape_facebook_dorks
                # facebook_dorks devuelve dict con hallazgos + eventos
                resultado = scrape_facebook_dorks(timeout=timeout)
                # Los eventos encontrados se devuelven para que el orquestador
                # los agregue al CSV (aditivo, nunca resta).
                eventos = resultado.get("eventos", []) or []
                metricas["eventos_encontrados"] = len(eventos)
                metricas["hallazgos_grupos"] = resultado.get("grupos", 0)
                metricas["hallazgos_paginas"] = resultado.get("paginas", 0)
                metricas["hallazgos_correos"] = resultado.get("correos", 0)
                metricas["total_hallazgos"] = resultado.get("total_hallazgos", 0)

            elif nombre == "instagram_dorks":
                from scrapers.instagram_dorks import scrape_instagram_dorks
                eventos = scrape_instagram_dorks(timeout=timeout)
                metricas["eventos_encontrados"] = len(eventos)

            elif nombre == "dorks_unificado":
                from scrapers.dorks_resultados import scrape_dorks_resultados
                eventos = scrape_dorks_resultados(dorks=dorks, motores=motores, timeout=timeout)
                metricas["eventos_encontrados"] = len(eventos)

            else:
                # Generic fallback using dorks_resultados
                from scrapers.dorks_resultados import buscar_y_clasificar
                eventos = buscar_y_clasificar(
                    dorks=dorks,
                    motores=motores,
                    timeout=timeout,
                )
                metricas["eventos_encontrados"] = len(eventos)

            metricas["dorks_procesados"] = len(dorks)
            metricas["tiempo_s"] = round(time.time() - t0, 2)

            # Update source stats
            if nombre in self._fuentes:
                self._fuentes[nombre]["ultima_ejecucion"] = (
                    datetime.now(timezone.utc).isoformat()
                )
                self._fuentes[nombre]["total_eventos"] = (
                    self._fuentes[nombre].get("total_eventos", 0) + len(eventos)
                )
                self._fuentes[nombre]["total_ejecuciones"] = (
                    self._fuentes[nombre].get("total_ejecuciones", 0) + 1
                )
                self._fuentes[nombre]["ultimos_metricas"] = metricas

            return {"eventos": eventos, "metricas": metricas}

        except ImportError as e:
            print(f"  ⚠️ {nombre}: import error: {e}")
            metricas["tiempo_s"] = round(time.time() - t0, 2)
            return {"eventos": [], "metricas": metricas}
        except Exception as e:
            print(f"  ❌ {nombre}: {type(e).__name__}: {e}")
            metricas["tiempo_s"] = round(time.time() - t0, 2)
            return {"eventos": [], "metricas": metricas}

    def agregar_eventos(self, eventos: List[Dict]) -> int:
        """Agrega eventos al repositorio local, deduplicando.

        Returns: número de eventos nuevos agregados.
        """
        if self._dedup is not None:
            nuevos = self._dedup.filtrar_nuevos(eventos)
            self._dedup.registrar_vistos(nuevos)
            self._dedup.guardar()
        else:
            existentes = self._cargar_repo()
            urls_existentes = {e.get("link") for e in existentes if isinstance(e, dict)}
            nuevos = [e for e in eventos if e.get("link") not in urls_existentes]

        self._agregar_al_repo(nuevos)
        return len(nuevos)

    def obtener_eventos(self) -> List[Dict]:
        """Devuelve la lista consolidada de eventos del repositorio."""
        return self._cargar_repo()

    def guardar_estado(self) -> None:
        """Persiste el estado del organizador."""
        estado_completo = {
            "fuentes": {},
            "estado": self._estado,
            "motores_disponibles": self._motores_disponibles,
        }
        for nombre, fuente in self._fuentes.items():
            estado_completo["fuentes"][nombre] = {
                "habilitado": fuente.get("habilitado", True),
                "ultima_ejecucion": fuente.get("ultima_ejecucion"),
                "total_eventos": fuente.get("total_eventos", 0),
                "total_ejecuciones": fuente.get("total_ejecuciones", 0),
                "num_dorks": len(fuente.get("dorks", [])),
                "motores": fuente.get("motores", []),
                "ultimos_metricas": fuente.get("ultimos_metricas", {}),
            }
        try:
            tmp = str(_ESTADO_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(estado_completo, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(_ESTADO_FILE))
        except (IOError, OSError):
            pass

    def cargar_estado(self) -> None:
        """Carga el estado previo del organizador."""
        if not _ESTADO_FILE.exists():
            return
        try:
            with open(_ESTADO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._estado = data.get("estado", {})
            self._motores_disponibles = data.get("motores_disponibles", [])
            for nombre, info in data.get("fuentes", {}).items():
                if nombre in self._fuentes:
                    self._fuentes[nombre]["habilitado"] = info.get("habilitado", True)
                    self._fuentes[nombre]["ultima_ejecucion"] = info.get("ultima_ejecucion")
                    self._fuentes[nombre]["total_eventos"] = info.get("total_eventos", 0)
                    self._fuentes[nombre]["total_ejecuciones"] = info.get("total_ejecuciones", 0)
                    self._fuentes[nombre]["ultimos_metricas"] = info.get("ultimos_metricas", {})
        except (json.JSONDecodeError, IOError):
            pass

    def _cargar_repo(self) -> List[Dict]:
        """Carga eventos del repositorio local."""
        if not _REPO_EVENTOS.exists():
            return []
        try:
            with open(_REPO_EVENTOS, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def _agregar_al_repo(self, eventos: List[Dict]) -> None:
        """Agrega eventos al repositorio (aditivo, con límite)."""
        existentes = self._cargar_repo()
        urls_existentes = {e.get("link") for e in existentes if isinstance(e, dict)}

        nuevos = []
        for ev in eventos:
            url = ev.get("link") or ""
            if url and url not in urls_existentes:
                nuevos.append(ev)
                urls_existentes.add(url)

        consolidados = existentes + nuevos

        # Trim to max size (keep most recent)
        if len(consolidados) > MAX_EVENTS_REPO:
            consolidados = consolidados[-MAX_EVENTS_REPO:]

        try:
            tmp = str(_REPO_EVENTOS) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(consolidados, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(_REPO_EVENTOS))
        except (IOError, OSError):
            pass

    def obtener_metricas_rendimiento(self) -> Dict:
        """Devuelve métricas de rendimiento por motor y fuente."""
        try:
            with open(_RENDIMIENTO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, FileNotFoundError):
            return {}

    # --- Dork generators ---

    def _generar_dorks_facebook(self) -> List[str]:
        """Genera dorks para Facebook groups/pages/emails."""
        config = self._cargar_config()
        subgeneros = config.get("subgeneros", ["psytrance"])
        paises = config.get("paises", [])

        todas_ciudades = []
        for pais in paises:
            ciudades = pais.get("ciudades", [])
            for ciudad in ciudades:
                todas_ciudades.append({"ciudad": ciudad, "pais": pais["nombre"]})

        dorks: List[str] = []

        GROUP_KEYWORDS = ["grupo", "group", "comunidad", "organizador", "promoter", "crew"]
        for sub in subgeneros:
            for c in todas_ciudades[:25]:
                ciudad = c["ciudad"]
                for kw in GROUP_KEYWORDS[:3]:
                    dorks.append(f'{sub} {ciudad} {kw} facebook')

        for sub in subgeneros:
            for kw in GROUP_KEYWORDS[:4]:
                dorks.append(f'{sub} {kw} facebook group')

        PAGE_KEYWORDS = ["página", "page", "oficial", "productora", "booking"]
        for sub in subgeneros[:8]:
            for c in todas_ciudades[:15]:
                ciudad = c["ciudad"]
                for kw in PAGE_KEYWORDS[:2]:
                    dorks.append(f'{sub} {ciudad} {kw} facebook page')

        EMAIL_KEYWORDS = ['"@gmail.com"', '"@outlook.com"', '"@protonmail.com"']
        for sub in subgeneros[:6]:
            for email_kw in EMAIL_KEYWORDS[:2]:
                dorks.append(f'{sub} organizador {email_kw} facebook')

        return list(dict.fromkeys(dorks))[:40]

    def _generar_dorks_instagram(self) -> List[str]:
        """Genera dorks para Instagram posts + profiles."""
        config = self._cargar_config()
        subgeneros = config.get("subgeneros", ["psytrance"])
        paises = config.get("paises", [])

        todas_ciudades = []
        for pais in paises:
            ciudades = pais.get("ciudades", [])
            for ciudad in ciudades:
                todas_ciudades.append({"ciudad": ciudad, "pais": pais["nombre"]})

        dorks: List[str] = []

        PSY_HASHTAGS = ["psytrance", "goa", "darkpsy", "forestpsy", "hitech", "psybient"]
        for tag in PSY_HASHTAGS[:8]:
            dorks.append(f'{tag} instagram event')
            dorks.append(f'{tag} instagram party')
            dorks.append(f'{tag} instagram festival')

        for sub in subgeneros[:10]:
            for c in todas_ciudades[:20]:
                ciudad = c["ciudad"]
                dorks.append(f'{sub} {ciudad} instagram event')
                dorks.append(f'{sub} {ciudad} instagram festival')

        event_kws = ["festival", "rave", "open air", "gathering", "2026"]
        for kw in event_kws[:4]:
            for sub in subgeneros[:6]:
                dorks.append(f'{sub} {kw} instagram')

        ORGANIZER_KEYWORDS = ["organizer", "organizador", "promoter", "booking", "crew"]
        for sub in subgeneros[:8]:
            for org_kw in ORGANIZER_KEYWORDS[:3]:
                dorks.append(f'{sub} {org_kw} instagram')

        return list(dict.fromkeys(dorks))[:40]

    def _generar_dorks_unificado(self) -> List[str]:
        """Genera dorks masivos unificados (todas las plataformas)."""
        # Delegate to dorks_resultados generator
        try:
            from scrapers.dorks_resultados import _generar_dorks_default
            return _generar_dorks_default()
        except ImportError:
            # Fallback
            return self._generar_dorks_facebook() + self._generar_dorks_instagram()

    def _cargar_config(self) -> Dict:
        ruta = Path(_PROJECT_ROOT) / "config_grupos.json"
        if not ruta.exists():
            return {"subgeneros": ["psytrance"], "paises": []}
        try:
            with open(ruta, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {"subgeneros": ["psytrance"], "paises": []}

    # --- JSON-RPC interface (optional) ---

    def json_rpc_handler(self, request: Dict) -> Dict:
        """Maneja requests JSON-RPC sobre stdio.

        Methods soportados:
          - search: Ejecuta búsquedas y devuelve eventos
          - get_events: Devuelve eventos del repositorio
          - get_status: Devuelve estado del organizador
          - register_source: Registra una nueva fuente
          - get_metrics: Devuelve métricas de rendimiento
        """
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        try:
            if method == "search":
                eventos = self.ejecutar_busquedas(
                    limite=params.get("limite", 20),
                    timeout_total=params.get("timeout", 300),
                )
                result = {"eventos": eventos, "count": len(eventos)}

            elif method == "get_events":
                eventos = self.obtener_eventos()
                limite = params.get("limite", 50)
                result = {"eventos": eventos[:limite], "total": len(eventos)}

            elif method == "get_status":
                result = {
                    "fuentes": {
                        n: {
                            "habilitado": f.get("habilitado"),
                            "total_eventos": f.get("total_eventos", 0),
                            "total_ejecuciones": f.get("total_ejecuciones", 0),
                        }
                        for n, f in self._fuentes.items()
                    },
                    "estado": self._estado,
                    "motores_disponibles": self._motores_disponibles,
                }

            elif method == "register_source":
                self.registrar_fuente(
                    nombre=params["nombre"],
                    dorks=params.get("dorks", []),
                    motores=params.get("motores", self._motores_disponibles),
                    timeout=params.get("timeout", 120),
                    habilitado=params.get("habilitado", True),
                )
                result = {"ok": True}

            elif method == "get_metrics":
                result = {
                    "fuentes": {n: f.get("ultimos_metricas", {}) for n, f in self._fuentes.items()},
                    "global": self._estado.get("metricas_ultima_ejecucion", {}),
                    "rendimiento_motores": self.obtener_metricas_rendimiento(),
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": req_id,
                }

            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id,
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": req_id,
            }

    def run_json_rpc_stdio(self) -> None:
        """Ejecuta el servidor JSON-RPC sobre stdin/stdout."""
        print("🔧 MCP Organizador: JSON-RPC mode (stdin/stdout)")
        print("  Methods: search, get_events, get_status, register_source, get_metrics")
        print("  Ctrl+C to exit")

        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                response = self.json_rpc_handler(request)
                print(json.dumps(response, ensure_ascii=False))
                sys.stdout.flush()
            except json.JSONDecodeError:
                response = {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                }
                print(json.dumps(response))
                sys.stdout.flush()
            except KeyboardInterrupt:
                break


# ---------------------------------------------------------------------------
# Convenience function for orquestador integration
# ---------------------------------------------------------------------------
def ejecutar_mcp_dorks(
    limite: int = 20,
    timeout_total: int = TIMEOUT_DEFAULT,
) -> List[Dict]:
    """Punto de entrada rápido para el orquestador.

    Crea un MCPOrganizador temporal, ejecuta búsquedas y devuelve eventos.
    """
    org = MCPOrganizador()
    return org.ejecutar_busquedas(limite=limite, timeout_total=timeout_total)


if __name__ == "__main__":
    if "--json-rpc" in sys.argv:
        org = MCPOrganizador()
        org.run_json_rpc_stdio()
    else:
        t0 = time.time()
        eventos = ejecutar_mcp_dorks()
        print(f"\nTiempo: {time.time() - t0:.0f}s, Eventos: {len(eventos)}")
        for e in eventos[:5]:
            print(f"  {e['nombre'][:50]} | {e.get('fecha', '?')} | {e.get('lugar', '?')}")