"""Loop de auto-mejora para el sistema de dorks.

Ejecuta periódicamente los módulos de dorks, registra métricas y ajusta
automáticamente parámetros (pausas, rotación de cookies, dorks máximos,
priorización de motores) escribiendo la configuración en
dorks_config_loop.json para que los scrapers la lean dinámicamente.

Respetando:
- Máximo 3 ejecuciones por sesión
- Timeout total de 10 minutos por sesión
- 1 petición Tor simultánea (semáforo global)
- No romper la ejecución normal de los scrapers

Hard rule: ALL requests go through Tor. Real IP never used.
"""
from __future__ import annotations

import io
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Semaphore
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import from google_stealth
from core.google_stealth import (
    save_loop_config,
    load_loop_config,
    _build_random_cookies,
    _save_google_cookies,
    _rotate_tor_and_cookies,
    verificar_tor,
    obtener_sesion_tor,
    _build_search_url,
)

# State file
_LOOP_STATE_FILE = Path(_PROJECT_ROOT) / "dorks_loop_estado.json"

# Limits
MAX_RUNS_PER_SESSION = 3
TOTAL_TIMEOUT_SECONDS = 600  # 10 minutes
MAX_CONCURRENT_TOR = 1

# Tor semaphore (global, 1 request at a time)
_TOR_SEMAPHORE = Semaphore(MAX_CONCURRENT_TOR)

# Block signal patterns to detect in output
_BLOCK_PATTERNS = {
    "captcha": re.compile(r"captcha|unusual traffic|our systems have detected", re.IGNORECASE),
    "recaptcha": re.compile(r"recaptcha|verify you are human", re.IGNORECASE),
    "sorry": re.compile(r"/sorry/|/sorry/index", re.IGNORECASE),
    "429": re.compile(r"\b429\b|too many requests", re.IGNORECASE),
    "403": re.compile(r"\b403\b|forbidden", re.IGNORECASE),
    "202": re.compile(r"\b202\b", re.IGNORECASE),
}

_DEFAULT_PARAMS = {
    "pausa_entre_dorks": [30, 60],
    "pausa_entre_reintentos": [45, 90],
    "max_dorks": 10,
    "cookies_file": str(Path(_PROJECT_ROOT) / "tor_data" / "google_cookies.json"),
    "rotar_cookies_cada": 1,
    "motores": ["google_playwright"],
    "timeout_total": 180,
    "max_reintentos": 3,
}


def _leer_estado() -> Dict[str, Any]:
    """Load loop state from disk."""
    if _LOOP_STATE_FILE.exists():
        try:
            with open(_LOOP_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "ejecuciones": [],
        "metricas_globales": {
            "total_dorks": 0,
            "total_bloqueos": 0,
            "total_resultados": 0,
            "bloqueos_por_tipo": {
                "captcha": 0, "recaptcha": 0, "sorry": 0,
                "429": 0, "403": 0, "202": 0,
            },
            "tiempo_total_s": 0.0,
        },
        "parametros": dict(_DEFAULT_PARAMS),
    }


def _guardar_estado(estado: Dict[str, Any]) -> None:
    """Save loop state to disk."""
    try:
        tmp = str(_LOOP_STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, str(_LOOP_STATE_FILE))
    except (IOError, OSError):
        pass


def _write_config_to_file(params: Dict[str, Any]) -> None:
    """Write current params to dorks_config_loop.json for scrapers to read."""
    save_loop_config(params)


def _ajustar_parametros(estado: Dict[str, Any]) -> Dict[str, Any]:
    """Adjust parameters based on accumulated metrics.

    Rules:
    - If block rate > 50%: increase pauses, rotate cookies every dork
    - If block rate < 10%: decrease pauses slightly, rotate cookies less often
    - If avg time per dork > 30s: reduce max dorks
    - If avg time per dork < 15s: increase max dorks
    """
    params = dict(estado.get("parametros", dict(_DEFAULT_PARAMS)))
    metricas = estado["metricas_globales"]
    total_dorks = metricas["total_dorks"]
    total_bloqueos = metricas["total_bloqueos"]

    if total_dorks == 0:
        return params

    block_rate = total_bloqueos / max(total_dorks, 1)
    avg_time_per_dork = metricas["tiempo_total_s"] / max(total_dorks, 1)

    # Adjust pauses based on block rate
    if block_rate > 0.5:
        # Many blocks: increase pauses
        min_pausa = min(params["pausa_entre_dorks"][0] + 5, 90)
        max_pausa = min(params["pausa_entre_dorks"][1] + 10, 120)
        params["pausa_entre_dorks"] = [min_pausa, max_pausa]
        min_retry = min(params["pausa_entre_reintentos"][0] + 10, 120)
        max_retry = min(params["pausa_entre_reintentos"][1] + 15, 150)
        params["pausa_entre_reintentos"] = [min_retry, max_retry]
        # Rotate cookies more frequently (every dork)
        params["rotar_cookies_cada"] = 1
        print(f"  📈 Auto-ajuste: block_rate={block_rate:.1%} → pausas ↑, cookies rotan cada dork")
    elif block_rate < 0.1:
        # Few blocks: decrease pauses slightly
        min_pausa = max(params["pausa_entre_dorks"][0] - 3, 20)
        max_pausa = max(params["pausa_entre_dorks"][1] - 5, 35)
        params["pausa_entre_dorks"] = [min_pausa, max_pausa]
        # Rotate cookies less frequently
        params["rotar_cookies_cada"] = min(
            params["rotar_cookies_cada"] + 1, 5)
        print(f"  📉 Auto-ajuste: block_rate={block_rate:.1%} → pausas ↓, cookies rotan cada {params['rotar_cookies_cada']} dorks")

    # Adjust max dorks based on average time
    if avg_time_per_dork > 30:
        params["max_dorks"] = max(params["max_dorks"] - 2, 3)
        print(f"  📉 Auto-ajuste: avg_time={avg_time_per_dork:.0f}s → max_dorks ↓ ({params['max_dorks']})")
    elif avg_time_per_dork < 15:
        params["max_dorks"] = min(params["max_dorks"] + 1, 15)
        print(f"  📈 Auto-ajuste: avg_time={avg_time_per_dork:.0f}s → max_dorks ↑ ({params['max_dorks']})")

    return params


class _OutputCapture:
    """Context manager to capture stdout/stderr from scraper calls."""
    def __init__(self):
        self.buffer = io.StringIO()
        self._old_stdout = None
        self._old_stderr = None

    def __enter__(self):
        self._old_stdout = sys.stdout
        self._old_stderr = sys.stderr
        sys.stdout = self.buffer
        sys.stderr = self.buffer
        return self

    def __exit__(self, *args):
        sys.stdout = self._old_stdout
        sys.stderr = self._old_stderr

    def get_output(self) -> str:
        return self.buffer.getvalue()


def _detect_blocks_in_output(output: str) -> Dict[str, int]:
    """Detect block signals in captured output text."""
    counts = {}
    for signal, pattern in _BLOCK_PATTERNS.items():
        matches = pattern.findall(output)
        counts[signal] = len(matches)
    return counts


def _ejecutar_dorks_modulo(
    modulo_name: str,
    params: Dict[str, Any],
    timeout: int,
    max_dorks: int,
) -> Dict[str, Any]:
    """Execute a dorks module and return metrics.

    Captures stdout to detect block signals and count them.
    Returns dict with: eventos, bloqueos, bloqueos_por_tipo, tiempo_s, resultado_raw.
    """
    metricas = {
        "modulo": modulo_name,
        "eventos": 0,
        "bloqueos": 0,
        "bloqueos_por_tipo": {},
        "tiempo_s": 0.0,
        "resultado_raw": None,
        "error": None,
    }

    t0 = time.time()
    capture = _OutputCapture()

    try:
        with capture:
            if modulo_name == "facebook_dorks":
                from scrapers.facebook_dorks import scrape_facebook_dorks
                with _TOR_SEMAPHORE:
                    resultado = scrape_facebook_dorks(limite=max_dorks)
                eventos = len(resultado.get("eventos", []))
                grupos = len(resultado.get("grupos", []))
                metricas["eventos"] = eventos + grupos
                metricas["resultado_raw"] = {
                    "eventos": eventos,
                    "grupos": grupos,
                    "organizadores": len(resultado.get("organizadores", set())),
                }

            elif modulo_name == "dorks_resultados":
                from scrapers.dorks_resultados import scrape_dorks_resultados
                with _TOR_SEMAPHORE:
                    resultado = scrape_dorks_resultados(limite=max_dorks)
                metricas["eventos"] = len(resultado)
                metricas["resultado_raw"] = {"eventos": len(resultado)}

            elif modulo_name == "instagram_dorks":
                from scrapers.instagram_dorks import scrape_instagram_dorks
                with _TOR_SEMAPHORE:
                    resultado = scrape_instagram_dorks(limite=max_dorks)
                metricas["eventos"] = len(resultado)
                metricas["resultado_raw"] = {"eventos": len(resultado)}

            else:
                metricas["error"] = f"Módulo desconocido: {modulo_name}"
                return metricas

    except Exception as e:
        metricas["error"] = f"{type(e).__name__}: {e}"
        metricas["tiempo_s"] = round(time.time() - t0, 2)
        return metricas

    # Detect blocks in output
    output = capture.get_output()
    bloqueos_tipo = _detect_blocks_in_output(output)
    total_bloqueos = sum(bloqueos_tipo.values())
    metricas["bloqueos"] = total_bloqueos
    metricas["bloqueos_por_tipo"] = bloqueos_tipo
    metricas["tiempo_s"] = round(time.time() - t0, 2)
    return metricas


def ejecutar_loop(
    max_rondas: int = 3,
    timeout_total: int = 600,
    modulos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute the dorks auto-improvement loop.

    Args:
        max_rondas: Maximum number of rounds (3 per session limit).
        timeout_total: Total timeout in seconds (600s = 10 min).
        modulos: List of module names to execute. Default: all 3.

    Returns:
        Summary dict with metrics, parameter changes, and results.
    """
    if modulos is None:
        modulos = ["facebook_dorks", "dorks_resultados", "instagram_dorks"]

    # Load previous state
    estado = _leer_estado()

    start_time = time.time()
    fin_total = start_time + timeout_total
    rondas_ejecutadas = 0

    print(f"🔄 Loop de auto-mejora: {min(max_rondas, MAX_RUNS_PER_SESSION)} rondas, "
          f"timeout {timeout_total}s")

    for ronda in range(min(max_rondas, MAX_RUNS_PER_SESSION)):
        if time.time() > fin_total:
            print(f"  ⏰ Loop: timeout global alcanzado en ronda {ronda+1}")
            break

        rondas_ejecutadas += 1
        print(f"\n--- Ronda {ronda+1}/{min(max_rondas, MAX_RUNS_PER_SESSION)} ---")

        # Adjust parameters before each round
        params = _ajustar_parametros(estado)

        # Write config to file for scrapers to read
        params_to_write = dict(params)
        params_to_write["timeout_total"] = max(
            int((fin_total - time.time()) / max(1, min(max_rondas - ronda, 3))), 60)
        _write_config_to_file(params_to_write)
        print(f"  ⚙️  Config escrita: dorks={params['max_dorks']}, "
              f"pausa_dorks={params['pausa_entre_dorks']}, "
              f"cookies_cada={params['rotar_cookies_cada']}")

        max_dorks = params["max_dorks"]
        timeout_ronda = params_to_write["timeout_total"]

        round_metrics = []
        for modulo in modulos:
            if time.time() > fin_total:
                break

            print(f"  📦 Ejecutando {modulo} (max {max_dorks} dorks, timeout {timeout_ronda}s)...")
            metricas = _ejecutar_dorks_modulo(modulo, params, timeout_ronda, max_dorks)
            round_metrics.append(metricas)

            # Update global metrics
            estado["metricas_globales"]["total_dorks"] += max_dorks
            estado["metricas_globales"]["total_resultados"] += metricas["eventos"]
            estado["metricas_globales"]["tiempo_total_s"] += metricas["tiempo_s"]

            if metricas["bloqueos"] > 0:
                estado["metricas_globales"]["total_bloqueos"] += metricas["bloqueos"]
                for tipo, count in metricas["bloqueos_por_tipo"].items():
                    if tipo in estado["metricas_globales"]["bloqueos_por_tipo"]:
                        estado["metricas_globales"]["bloqueos_por_tipo"][tipo] += count

            if metricas["error"]:
                print(f"    ❌ {modulo}: {metricas['error']}")
            else:
                block_str = ""
                if metricas["bloqueos"] > 0:
                    block_str = f" | {metricas['bloqueos']} bloqueos"
                print(f"    ✅ {modulo}: {metricas['eventos']} eventos "
                      f"en {metricas['tiempo_s']}s{block_str}")

        # Update params in estado for next round
        estado["parametros"] = params

        # Save state after each round
        estado["ejecuciones"].append({
            "ronda": ronda + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metricas": round_metrics,
            "parametros_usados": params,
        })
        _guardar_estado(estado)

        # Between rounds: wait if time remains
        if time.time() < fin_total and ronda < max_rondas - 1:
            pausa = random.uniform(10, 20)
            print(f"  ⏳ Pausa entre rondas: {pausa:.0f}s...")
            time.sleep(pausa)

    # Final summary
    elapsed = round(time.time() - start_time, 1)
    metricas_finales = estado["metricas_globales"]
    params_finales = estado["parametros"]

    cambios = []
    if params_finales["pausa_entre_dorks"] != _DEFAULT_PARAMS["pausa_entre_dorks"]:
        cambios.append(f"Pausa dorks: {_DEFAULT_PARAMS['pausa_entre_dorks']} → {params_finales['pausa_entre_dorks']}s")
    if params_finales["pausa_entre_reintentos"] != _DEFAULT_PARAMS["pausa_entre_reintentos"]:
        cambios.append(f"Pausa reintentos: {_DEFAULT_PARAMS['pausa_entre_reintentos']} → {params_finales['pausa_entre_reintentos']}s")
    if params_finales["max_dorks"] != _DEFAULT_PARAMS["max_dorks"]:
        cambios.append(f"Max dorks: {_DEFAULT_PARAMS['max_dorks']} → {params_finales['max_dorks']}")
    if params_finales["rotar_cookies_cada"] != _DEFAULT_PARAMS["rotar_cookies_cada"]:
        cambios.append(f"Cookie rotation: cada {_DEFAULT_PARAMS['rotar_cookies_cada']} → cada {params_finales['rotar_cookies_cada']} dorks")

    summary = {
        "rondas_ejecutadas": rondas_ejecutadas,
        "tiempo_total_s": elapsed,
        "metricas_finales": metricas_finales,
        "parametros_finales": params_finales,
        "cambios_realizados": cambios,
    }

    # Save final state
    _guardar_estado(estado)

    print(f"\n📊 Loop completado: {rondas_ejecutadas} rondas en {elapsed}s")
    print(f"   Eventos totales: {metricas_finales['total_resultados']}")
    print(f"   Bloqueos totales: {metricas_finales['total_bloqueos']}")
    if metricas_finales['total_bloqueos'] > 0:
        print(f"   Bloqueos por tipo: {metricas_finales['bloqueos_por_tipo']}")
    if cambios:
        print(f"   Cambios: {', '.join(cambios)}")

    return summary


if __name__ == "__main__":
    result = ejecutar_loop(max_rondas=1, timeout_total=180)
    print(json.dumps(result, indent=2, ensure_ascii=False))
