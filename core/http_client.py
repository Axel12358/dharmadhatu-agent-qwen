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

import json
import os
import random
import time
from pathlib import Path
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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Proxy SOCKS5 de Tor (por defecto). socks5h:// resuelve DNS dentro del túnel.
TOR_PROXIES = {
    "http": "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}


def _load_proxy_url() -> Optional[str]:
    """Proxy residencial de TERCEROS (NUNCA la IP del usuario).

    Orden: 1) env DHARMA_PROXY_URL (más seguro, fuera del repo),
           2) config_modulos.json -> proxy.url.
    Devuelve la URL del proxy o None (-> se usa Tor).
    """
    env = os.environ.get("DHARMA_PROXY_URL")
    if env and env.strip():
        return env.strip()
    try:
        cfg = json.loads((_PROJECT_ROOT / "config_modulos.json").read_text(encoding="utf-8"))
        p = cfg.get("proxy") or {}
        if p.get("enabled") and p.get("url"):
            return str(p["url"]).strip()
    except Exception:
        pass
    return None


def get_active_proxies() -> dict:
    """Devuelve el dict de proxies a usar.

    Si hay proxy residencial de terceros configurado, lo usa (nunca la IP del
    usuario). Si es socks5, lo fuerza a socks5h:// para que el DNS también vaya
    por el proxy y no filtre la IP real del usuario.
    """
    url = _load_proxy_url()
    if url:
        if url.startswith("socks5://"):
            url = "socks5h://" + url[len("socks5://"):]
        return {"http": url, "https": url}
    return TOR_PROXIES

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
    """Devuelve una sesión curl_cffi con TLS de navegador + proxy activo.

    El proxy activo es: residencial de terceros si está configurado, si no Tor.
    En cualquier caso NUNCA se usa la IP del usuario.
    Retorna None si curl_cffi no está disponible.
    """
    if Session is None:
        return None
    proxies = get_active_proxies()
    profile = impersonate or IMPERSONATE_DEFAULT
    try:
        session = Session(
            impersonate=profile,
            proxies=proxies,
            headers={"User-Agent": _random_user_agent()},
        )
    except Exception:
        try:
            session = Session(
                proxies=proxies,
                headers={"User-Agent": _random_user_agent()},
            )
        except Exception:
            return None
    return session


def get_html(url: str, timeout: int = 15, impersonate: Optional[str] = None) -> Optional[str]:
    """Descarga el HTML de `url` vía el proxy activo (residencial o Tor) + curl_cffi.

    - TLS de navegador + proxy (nunca IP del usuario).
    - Rota el User-Agent en cada llamada.
    - Devuelve el HTML (str) o None si falla.

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


def activar_tor_en_requests() -> bool:
    """Monkeypatch global: hace que `requests.get/post` pasen por Tor (curl_cffi).

    Parchea el módulo `requests` para que los scrapers que usan requests
    directamente NUNCA expongan la IP del usuario. Idempotente.
    """
    import requests as _requests

    if getattr(_requests, "_dharma_tor_patched", False):
        return True
    if Session is None:
        return False

    _TOR = {}

    _ORIG = {
        "get": _requests.get,
        "post": _requests.post,
        "request": _requests.request,
        "Session": _requests.Session,
    }
    # Guardamos los originales como atributo por si algún scraper los necesita.
    _requests._dharma_tor_orig = _ORIG

    def _tor_get(url, *a, **k):
        # Nueva sesión por llamada: rota UA y es thread-safe entre scrapers.
        try:
            sesion = crear_sesion_tor()
            if sesion is None:
                return _ORIG["get"](url, *a, **k)
            try:
                return sesion.get(url, *a, **k)
            finally:
                try:
                    sesion.close()
                except Exception:
                    pass
        except Exception:
            return _requests.Response()

    def _tor_post(url, *a, **k):
        try:
            sesion = crear_sesion_tor()
            if sesion is None:
                return _ORIG["post"](url, *a, **k)
            try:
                return sesion.post(url, *a, **k)
            finally:
                try:
                    sesion.close()
                except Exception:
                    pass
        except Exception:
            return _requests.Response()

    _requests.get = _tor_get
    _requests.post = _tor_post
    _requests._dharma_tor_patched = True
    return True
