#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tor Manager — Gestión centralizada del proxy Tor para módulos de dorks.

Provee:
  - obtener_proxy_tor(): dict de proxy para requests
  - obtener_sesion_tor(): requests.Session preconfigurada con Tor + headers
  - tor_disponible(): chequeo rápido del puerto 9050
  - renovar_identidad_tor(): rotación de IP vía ControlPort (opcional)

Si Tor no está disponible, los módulos de dorks fallan silenciosamente
(devuelven 0 eventos) y el bot sigue funcionando sin proxy.

Uso:
    from core.tor_manager import obtener_sesion_tor, tor_disponible
    sesion = obtener_sesion_tor()
    if sesion:
        r = sesion.get("https://example.com")
"""

from __future__ import annotations

import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Constants ---
TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
CHECK_TIMEOUT = 2  # seconds for port check

# ControlPort password (must match HashedControlPassword in torrc)
TOR_CONTROL_PASSWORD = "dharmadhatu_tor_pass"

# Paths used to (re)start Tor via subprocess if ControlPort is unavailable
TORRC_PATH = Path(_PROJECT_ROOT) / "tor_data" / "torrc"
TOR_BIN = "/usr/local/bin/tor"

# Restart Tor automatically after this many successful searches (per session)
TOR_RENEW_EVERY_SEARCHES = 45

# --- Tor Pool integration ---
try:
    from core.tor_pool import (
        iniciar_pool as _pool_iniciar,
        obtener_sesion_tor as _pool_obtener_sesion,
        rotar_tor as _pool_rotar,
        verificar_tor as _pool_verificar,
        obtener_siguiente_identidad as _pool_siguiente,
        marcar_circuito_bloqueado as _pool_marcar_bloqueado,
        IDENTIDADES_TOR as _POOL_IDENTIDADES,
    )
    _POOL_AVAILABLE = True
except ImportError:
    _POOL_AVAILABLE = False
    _pool_iniciar = None
    _pool_obtener_sesion = None
    _pool_rotar = None
    _pool_verificar = None

# Cached state
_tor_available: Optional[bool] = None
_tor_checked_at: float = 0.0
_TOR_CHECK_TTL = 60  # recheck every 60s

# Ensure pool is initialized on first import
_tor_pool_iniciado = False
if _POOL_AVAILABLE:
    try:
        if _pool_verificar:
            _tor_available = _pool_verificar()
            _tor_checked_at = time.time()
        if not _tor_available:
            _pool_iniciar()
            _tor_pool_iniciado = True
            _tor_available = _pool_verificar()
    except Exception:
        pass

# Browser-like User-Agent pool (rotated per session creation)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def tor_disponible() -> bool:
    """Verifica si Tor SOCKS5 está disponible en 127.0.0.1:9050.

    Cachea el resultado por _TOR_CHECK_TTL segundos.
    Delega a tor_pool.verificar_tor() si está disponible.
    """
    global _tor_available, _tor_checked_at

    now = time.time()
    if _tor_available is not None and (now - _tor_checked_at) < _TOR_CHECK_TTL:
        return _tor_available

    # Usar tor_pool si está disponible (verifica todos los circuitos)
    if _POOL_AVAILABLE and _pool_verificar is not None:
        _tor_available = _pool_verificar()
        _tor_checked_at = now
        return _tor_available

    # Fallback: chequeo directo de puerto
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(CHECK_TIMEOUT)
        s.connect((TOR_SOCKS_HOST, TOR_SOCKS_PORT))
        s.close()
        _tor_available = True
    except (socket.error, OSError, ConnectionRefusedError):
        _tor_available = False

    _tor_checked_at = now
    return _tor_available


def obtener_proxy_tor() -> Optional[Dict[str, str]]:
    """Devuelve dict de proxy para requests.

    Si hay proxy residencial de terceros configurado (nunca IP del usuario),
    lo devuelve. Si no, devuelve el proxy Tor (o None si Tor no disponible).

    Note: usa socks5h:// (no socks5://) para que DNS se resuelva vía túnel,
    previniendo DNS leaks de la IP real.
    """
    from core.http_client import get_active_proxies as _gap, _load_proxy_url as _lp
    if _lp():
        return _gap()
    if not tor_disponible():
        return None
    proxy_url = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
    return {"http": proxy_url, "https": proxy_url}


def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


def _random_headers() -> Dict[str, str]:
    return {
        "User-Agent": _random_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": random.choice([
            "en-US,en;q=0.9",
            "es-ES,es;q=0.9,en;q=0.8",
            "en-GB,en;q=0.9",
            "de-DE,de;q=0.9,en;q=0.8",
            "fr-FR,fr;q=0.9,en;q=0.8",
        ]),
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def obtener_sesion_tor(
    usar_proxy: bool = True,
    headers_extra: Optional[Dict[str, str]] = None,
) -> "requests.Session":
    """Devuelve una requests.Session configurada con Tor proxy y headers aleatorios.

    Si Tor no está disponible y usar_proxy=True, la sesión funciona SIN proxy
    (modo degradado). Si usar_proxy=False, siempre retorna sin proxy.

    Returns:
        requests.Session lista para usar.
    """
    import requests  # import local to avoid circular at module level

    session = requests.Session()

    # Proxy
    if usar_proxy:
        proxy = obtener_proxy_tor()
        if proxy:
            session.proxies.update(proxy)

    # Headers con UA rotado
    session.headers.update(_random_headers())

    return session


def obtener_sesion_directa(
    headers_extra: Optional[Dict[str, str]] = None,
) -> "requests.Session":
    """Devuelve una requests.Session SIN proxy (conexión directa).

    Útil para scrapers que no deben usar Tor.
    """
    import requests

    session = requests.Session()
    session.headers.update(_random_headers())
    return session


def _reiniciar_proceso_tor() -> bool:
    """Reinicia el proceso Tor vía subprocess (fallback si ControlPort no sirve).

    Mata el tor en ejecución y arranca uno nuevo con el torrc del proyecto.
    Devuelve True si el nuevo proceso queda escuchando en 9050.
    """
    try:
        # 1. Matar procesos tor existentes del proyecto
        try:
            out = subprocess.run(
                ["pgrep", "-f", f"tor -f {TORRC_PATH}"],
                capture_output=True, text=True, timeout=10,
            )
            for pid in out.stdout.split():
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(2)

        # 2. Arrancar nuevo proceso en background
        if not TORRC_PATH.exists():
            return False
        subprocess.Popen(
            [TOR_BIN, "-f", str(TORRC_PATH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # 3. Esperar a que el SOCKS port abra
        for _ in range(15):
            if tor_disponible():
                time.sleep(1)  # dejar asentar el circuito
                return True
            time.sleep(1)
        return False
    except Exception:
        return False


def renovar_identidad_tor() -> bool:
    """Rota la IP de Tor.

    Estrategia (en orden, la primera que funcione):
      1. tor_pool: rotar_tor() con gestión profesional de circuitos.
      2. ControlPort 9051 vía stem con autenticación por contraseña (NEWNYM).
      3. Fallback: reiniciar el proceso Tor vía subprocess (nuevo circuito).

    Devuelve True si la identidad se renovó (o el proceso reinició).
    """
    # Intento 0: tor_pool (gestión profesional)
    if _POOL_AVAILABLE and _pool_rotar is not None:
        try:
            # Rotar el circuito menos usado/no bloqueado
            idx = _pool_siguiente() if _pool_siguiente else 0
            return _pool_rotar(idx)
        except Exception:
            pass

    # Intento 1: ControlPort + stem
    try:
        from stem import Signal
        from stem.control import Controller

        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            try:
                controller.authenticate(password=TOR_CONTROL_PASSWORD)
            except Exception:
                controller.authenticate()
            controller.signal(Signal.NEWNYM)
            time.sleep(3)  # Tor necesita reconstruir el circuito
            return True
    except Exception:
        pass

    # Intento 2: reinicio del proceso Tor
    return _reiniciar_proceso_tor()


def crear_sesion_inteligente(
    usar_tor: bool = True,
    fallback_directo: bool = True,
) -> "requests.Session":
    """Crea una sesión de requests de forma inteligente.

    Si usar_tor=True y Tor está disponible → usa Tor.
    Si usar_tor=True y Tor NO está disponible y fallback_directo=True → conexión directa.
    Si usar_tor=False → siempre conexión directa.

    Esta es la función recomendada para módulos de dorks.
    """
    import requests

    session = requests.Session()

    if usar_tor:
        proxy = obtener_proxy_tor()
        if proxy:
            session.proxies.update(proxy)
        elif not fallback_directo:
            # Tor requerido pero no disponible → devolver sesión sin proxy de todos modos
            # para que el módulo falle silenciosamente con 0 eventos
            pass

    session.headers.update(_random_headers())
    return session


def crear_sesion_busqueda(usar_tor: bool = False) -> "requests.Session":
    """Crea una sesión optimizada para búsqueda en motores de búsqueda.

    Por defecto usa conexión DIRECTA (no Tor) porque la mayoría de motores
    de búsqueda bloquean exit nodes de Tor. Tor se usa solo cuando se
    solicita explícitamente.

    Para uso con Tor, pasar usar_tor=True. En ese caso, el caller debe
    estar preparado para recibir 0 resultados si el motor bloquea Tor.
    """
    import requests

    session = requests.Session()

    if usar_tor:
        proxy = obtener_proxy_tor()
        if proxy:
            session.proxies.update(proxy)

    session.headers.update(_random_headers())
    return session


if __name__ == "__main__":
    print(f"Tor disponible: {tor_disponible()}")
    proxy = obtener_proxy_tor()
    print(f"Proxy dict: {proxy}")
    sesion = obtener_sesion_tor()
    print(f"Sesión headers: {dict(list(sesion.headers.items())[:3])}")
    if proxy:
        print(f"Sesión proxies: {dict(sesion.proxies)}")
        # Test
        try:
            r = sesion.get("https://api.ipify.org?format=json", timeout=30)
            print(f"IP vía Tor: {r.json()['ip']}")
        except Exception as e:
            print(f"Error test Tor: {e}")
    else:
        print("Tor no disponible — sesión sin proxy")
