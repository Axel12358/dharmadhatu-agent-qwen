#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tor Utilities — Rotación de identidad y utilidades Tor centralizadas.

Proporciona:
  - rotar_tor(): rota la identidad Tor (Stem NEWNYM si ControlPort 9051 está activo,
    o reinicio de proceso si no).
  - verificar_tor(): comprueba si Tor SOCKS 9050 está activo.
  - obtener_sesion_tor(): requests.Session con proxy Tor + headers rotados.

No instala librerías. Usa subprocess + socket (Stem opcional si está disponible).
"""

from __future__ import annotations

import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Configuración Tor ---
TOR_SOCKS_HOST = "127.0.0.1"
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
TOR_CONTROL_PASSWORD = "dharmadhatu_tor_pass"
TOR_BIN = "/usr/local/bin/tor"
TOR_DATA_DIR = Path(_PROJECT_ROOT) / "tor_data"
TORRC_PATH = TOR_DATA_DIR / "torrc"
STARTUP_WAIT = 8  # segundos esperando arranque

# User-Agent pool
_user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def verificar_tor() -> bool:
    """Comprueba si Tor SOCKS está activo en 127.0.0.1:9050."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((TOR_SOCKS_HOST, TOR_SOCKS_PORT))
        s.close()
        return True
    except (socket.error, OSError, ConnectionRefusedError):
        return False


def _puerto_control_activo() -> bool:
    """Comprueba si el ControlPort 9051 está activo."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((TOR_SOCKS_HOST, TOR_CONTROL_PORT))
        s.close()
        return True
    except (socket.error, OSError, ConnectionRefusedError):
        return False


def rotar_tor() -> bool:
    """Rota la identidad Tor.

    Estrategia:
      1. Si ControlPort 9051 está activo → usa Stem (NEWNYM).
      2. Si ControlPort no responde → reinicia el proceso Tor con subprocess
         y espera STARTUP_WAIT segundos.

    Returns True si la rotación/arranque tuvo éxito.
    """
    # Intento 1: Stem + ControlPort
    if _puerto_control_activo():
        try:
            from stem import Signal
            from stem.control import Controller
            with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
                try:
                    controller.authenticate(password=TOR_CONTROL_PASSWORD)
                except Exception:
                    controller.authenticate()
                controller.signal(Signal.NEWNYM)
                time.sleep(5)
                return True
        except ImportError:
            pass  # Stem no está instalado, caer a reinicio de proceso
        except Exception:
            pass

    # Intento 2: reinicio del proceso Tor
    try:
        # Matar procesos Tor existentes
        subprocess.run(["pkill", "-f", "tor -f"], capture_output=True, timeout=5)
        time.sleep(2)

        # Arrancar nuevo proceso Tor
        if not TORRC_PATH.exists():
            TOR_DATA_DIR.mkdir(parents=True, exist_ok=True)
            TORRC_PATH.write_text(
                f"SocksPort {TOR_SOCKS_PORT}\n"
                f"ControlPort {TOR_CONTROL_PORT}\n"
                f"DataDirectory {TOR_DATA_DIR}\n"
                f"RunAsDaemon 0\n"
            )

        proc = subprocess.Popen(
            [TOR_BIN, "-f", str(TORRC_PATH)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Esperar a que arranque
        for _ in range(20):
            if verificar_tor():
                time.sleep(STARTUP_WAIT)
                return True
            time.sleep(0.5)
        return False
    except Exception:
        return False


def obtener_sesion_tor() -> "requests.Session":
    """Devuelve una requests.Session con proxy activo (residencial o Tor).

    Usa la IP real? NUNCA. Si hay proxy residencial de terceros configurado,
    lo usa; si no, usa Tor. Si ni Tor ni proxy, levanta excepción clara.
    """
    import requests

    from core.http_client import get_active_proxies as _gap, _load_proxy_url as _lp
    if _lp():
        s = requests.Session()
        s.proxies.update(_gap())
        s.headers.update({"User-Agent": random.choice(_user_agents)})
        return s

    if not verificar_tor():
        raise RuntimeError("Tor no disponible en 127.0.0.1:9050. "
                           "Inicia Tor antes de hacer peticiones.")

    session = requests.Session()
    proxy_url = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
    session.proxies.update({"http": proxy_url, "https": proxy_url})
    session.headers.update({
        "User-Agent": random.choice(_user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def obtener_sesion_tor_flexible(usar_tor: bool = True) -> "requests.Session":
    """Versión flexible: si usar_tor=True y Tor está activo, usa proxy Tor.
    Si usar_tor=True pero Tor NO está activo → lanza excepción (nunca IP real).
    Si usar_tor=False → sesión sin proxy (solo para testing local, NUNCA en scraping).
    """
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(_user_agents),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if usar_tor:
        if not verificar_tor():
            raise RuntimeError("Tor no disponible. No se permite scraping con IP real.")
        proxy_url = f"socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    return session


if __name__ == "__main__":
    print(f"Tor verificado: {verificar_tor()}")
    if verificar_tor():
        s = obtener_sesion_tor()
        print(f"Sesión proxy: {s.proxies}")
