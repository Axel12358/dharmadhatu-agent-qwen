#!/usr/bin/env python3
"""
Cliente HTTP compartido para Dharmadhatu Bot v5.

Centraliza el acceso a sitios que bloquean Tor/requests normal (Facebook,
Instagram, etc.) usando curl_cffi con impersonación de TLS (Safari/Chrome) y
un proxy SOCKS5 de Tor (socks5h://127.0.0.1:9050) para no usar la IP real.

Principio: sumar nunca restar. No toca las fuentes estables; este módulo solo
se usa donde se indica explícitamente (Facebook, Instagram). Si la sesión
falla, get_html() devuelve None y el llamador omite la estrategia.

Requisitos (en el venv):
    pip install curl_cffi fake-useragent
"""

from __future__ import annotations

import random
import time
from typing import Optional

# curl_cffi aporta TLS fingerprint de navegador real + proxies SOCKS5.
try:
    from curl_cffi.requests import Session
except Exception:  # pragma: no cover - dependencia presente en el venv
    Session = None

try:
    from fake_useragent import UserAgent
except Exception:  # pragma: no cover
    UserAgent = None

# Proxy SOCKS5 de Tor. socks5h:// resuelve DNS dentro del túnel (sin leak de IP).
TOR_PROXIES = {
    "http": "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}

# Perfil de TLS a impersonar. Se elige chrome110 (funciona bien contra FB).
# Si fb lo rechaza, se puede cambiar a "safari15_3" o "safari16_0".
IMPERSONATE_DEFAULT = "chrome110"

_POOL_USERAGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_3 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/15.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_2) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.3 Safari/605.1.15",
]


def _random_user_agent() -> str:
    """User-Agent rotativo: fake_useragent si está disponible, si no pool fijo."""
    if UserAgent is not None:
        try:
            return UserAgent().random
        except Exception:
            pass
    return random.choice(_POOL_USERAGENTS)


def crear_sesion_tor(impersonate: Optional[str] = None) -> Optional["Session"]:
    """Devuelve una sesión curl_cffi con TLS de navegador + proxy Tor.

    Retorna None si curl_cffi no está disponible (para que el llamador pueda
    omitir la estrategia en vez de romper el flujo).
    """
    if Session is None:
        return None
    profile = impersonate or IMPERSONATE_DEFAULT
    try:
        session = Session(
            impersonate=profile,
            proxies=TOR_PROXIES,
            headers={"User-Agent": _random_user_agent()},
        )
    except Exception:
        # Algún perfil de impersonación puede no existir en esta versión.
        # Reintentar sin impersonate (igual bloquea Tor, pero no rompe).
        try:
            session = Session(
                proxies=TOR_PROXIES,
                headers={"User-Agent": _random_user_agent()},
            )
        except Exception:
            return None
    return session


def get_html(url: str, timeout: int = 15, impersonate: Optional[str] = None) -> Optional[str]:
    """Descarga el HTML de `url` vía Tor + curl_cffi.

    - Usa una sesión con TLS de navegador y proxy SOCKS5 (sin IP real).
    - Rota el User-Agent en cada llamada.
    - Devuelve el HTML (str) o None si falla (timeout, bloqueo, excepción).

    El llamador debe tratar None como "omitir estrategia".
    """
    session = crear_sesion_tor(impersonate=impersonate)
    if session is None:
        return None
    try:
        # Rotar UA por petición para diversificar la huella.
        try:
            session.headers.update({"User-Agent": _random_user_agent()})
        except Exception:
            pass
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        if resp is None:
            return None
        if getattr(resp, "status_code", 0) not in (0, 200):
            # curl_cffi puede devolver 0 en algunos errores de tunnel.
            if getattr(resp, "status_code", 0) not in (200,):
                # Aceptamos 200; cualquier otro status lo tratamos como fallo suave.
                if getattr(resp, "status_code", 0) >= 400:
                    return None
        text = getattr(resp, "text", None)
        if not text:
            return None
        return text
    except Exception:
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass
