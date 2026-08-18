#!/usr/bin/env python3
"""
Rotación de recursos compartidos (Dharmadhatu Bot v5).

Carga User-Agents y proxies desde fuentes compartidas del proyecto:
- User-Agents: lista propia + la de `scrapers/anti_block` (si está disponible).
- Proxies: `proxies.txt` en la raíz del proyecto (línea = `http://ip:puerto`).

Expone `obtener_user_agent()` y `obtener_proxy()` con rotación round-robin,
además de `obtener_proxies_dict()` en formato compatible con `requests`.
"""

import random
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
PROXY_FILE = str(Path(_PROJECT_ROOT) / "proxies.txt")

# Lista base de User-Agents de respaldo (por si anti_block no está disponible).
_UA_BASE = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
]

# Lista consolidada de User-Agents (se rellena en el primer acceso).
_USER_AGENTS: Optional[List[str]] = None
_user_agents: Optional[List[str]] = None
_proxy_lista: Optional[List[str]] = None
_offset_ua = 0
_offset_proxy = 0


def _get_anti_block_ua() -> List[str]:
    """Devuelve la lista de User-Agents de scrapers/anti_block si existe."""
    try:
        from scrapers.anti_block import USER_AGENTS
        if isinstance(USER_AGENTS, list) and USER_AGENTS:
            return list(USER_AGENTS)
    except Exception:
        pass
    return []


def listar_user_agents() -> List[str]:
    """Lista consolidada de User-Agents del proyecto."""
    global _user_agents
    if _user_agents is None:
        extras = _get_anti_block_ua()
        _user_agents = list(dict.fromkeys(_UA_BASE + extras))
    return _user_agents


def _get_anti_block_ua() -> List[str]:
    try:
        from scrapers.anti_block import USER_AGENTS
        return list(USER_AGENTS) if isinstance(USER_AGENTS, list) else []
    except Exception:
        return []


def obtener_user_agent(random_opcion: bool = True) -> str:
    """Devuelve un User-Agent. Rotación round-robin o aleatoria."""
    global _offset_ua
    uas = listar_user_agents()
    if not uas:
        return "Mozilla/5.0 (compatible; DharmadhatuBot/5.0)"
    if random_opcion:
        return random.choice(uas)
    _offset_ua = (_offset_ua + 1) % len(uas)
    return uas[_offset_ua]


def listar_proxies() -> List[str]:
    """Lista de proxies desde proxies.txt (formato `http://ip:puerto` por línea)."""
    global _proxy_lista
    if _proxy_lista is not None:
        return _proxy_lista
    proxies = []
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for linea in f:
                p = linea.strip()
                if not p or p.startswith("#"):
                    continue
                if "://" not in p:
                    p = f"http://{p}"
                proxies.append(p)
    except OSError:
        pass
    _proxy_lista = proxies
    return proxies


def obtener_proxy(random_opcion: bool = True) -> Optional[str]:
    """Devuelve un proxy rotativo o None si no hay proxies configurados."""
    global _offset_proxy
    proxies = listar_proxies()
    if not proxies:
        return None
    if random_opcion:
        return random.choice(proxies)
    _offset_proxy = (_offset_proxy + 1) % len(proxies)
    return proxies[_offset_proxy]


def obtener_proxies_dict() -> Optional[Dict[str, str]]:
    """Dict para `requests` (http/https) o None si no hay proxies.

    Prioriza Tor (socks5h://127.0.0.1:9050) si está disponible — la IP real
    nunca debe usarse para scraping. Si Tor no está activo, cae a proxies.txt.
    """
    # Tor tiene prioridad: nunca usar IP real
    try:
        from core.tor_pool import verificar_tor, obtener_sesion_tor
        if verificar_tor():
            return {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
    except Exception:
        pass

    proxy = obtener_proxy()
    if proxy is None:
        return None
    return {"http": proxy, "https": proxy}


def obtener_proxies_tor() -> Optional[Dict[str, str]]:
    """Dict de proxies Tor (socks5h://127.0.0.1:9050) o None si Tor no disponible."""
    try:
        from core.tor_pool import verificar_tor
        if verificar_tor():
            return {"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}
    except Exception:
        pass
    return None


def headers_con_ua(extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Headers HTTP estándar con un User-Agent rotado."""
    headers = {
        "User-Agent": obtener_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    if extra:
        headers.update(extra)
    return headers


if __name__ == "__main__":
    print(f"User-Agents disponibles: {len(listar_user_agents())}")
    print(f"Proxies disponibles: {len(listar_proxies())}")
    print(f"UA de ejemplo: {obtener_user_agent()[:80]}")
    print(f"Proxy de ejemplo: {obtener_proxy()}")