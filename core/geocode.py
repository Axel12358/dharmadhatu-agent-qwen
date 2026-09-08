"""Geocodificación real de `lugar` -> (pais, continente, subcontinente).

Fuente: Nominatim (OpenStreetMap) vía Tor, con caché en disco y throttle para
no ser bloqueado. La tabla COUNTRY_INFO mapea el country_code ISO-2 a nombres
en español y continente/subcontinente, coincidiendo con el vocabulario del CSV.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GEO_CACHE = PROJECT_ROOT / "geo_cache.json"
NOM_URL = "https://nominatim.openstreetmap.org/search"
# Proxy activo: residencial de terceros si está configurado, si no Tor. Nunca IP real.
from core.http_client import get_active_proxies
PROXIES = get_active_proxies()
_UA = "dharmadhatu_bot/1.0 (eventos psytrance)"
_last = 0.0

# iso2 -> (pais_es, continente_es, subcontinente_es)
COUNTRY_INFO = {
    "AT": ("Austria", "Europa", "Europa occidental"),
    "CH": ("Suiza", "Europa", "Europa occidental"),
    "DE": ("Alemania", "Europa", "Europa occidental"),
    "ES": ("España", "Europa", "Europa meridional"),
    "FR": ("Francia", "Europa", "Europa occidental"),
    "GB": ("Reino Unido", "Europa", "Europa occidental"),
    "IE": ("Irlanda", "Europa", "Europa occidental"),
    "PT": ("Portugal", "Europa", "Europa meridional"),
    "IT": ("Italia", "Europa", "Europa meridional"),
    "GR": ("Grecia", "Europa", "Europa meridional"),
    "NL": ("Países Bajos", "Europa", "Europa occidental"),
    "BE": ("Bélgica", "Europa", "Europa occidental"),
    "SE": ("Suecia", "Europa", "Europa septentrional"),
    "NO": ("Noruega", "Europa", "Europa septentrional"),
    "DK": ("Dinamarca", "Europa", "Europa septentrional"),
    "FI": ("Finlandia", "Europa", "Europa septentrional"),
    "IS": ("Islandia", "Europa", "Europa septentrional"),
    "PL": ("Polonia", "Europa", "Europa oriental"),
    "CZ": ("República Checa", "Europa", "Europa oriental"),
    "HU": ("Hungría", "Europa", "Europa oriental"),
    "HR": ("Croacia", "Europa", "Europa meridional"),
    "SI": ("Eslovenia", "Europa", "Europa meridional"),
    "RS": ("Serbia", "Europa", "Europa meridional"),
    "BG": ("Bulgaria", "Europa", "Europa oriental"),
    "RO": ("Rumanía", "Europa", "Europa oriental"),
    "UA": ("Ucrania", "Europa", "Europa oriental"),
    "RU": ("Rusia", "Europa", "Europa oriental"),
    "TR": ("Turquía", "Asia", "Asia occidental"),
    "IL": ("Israel", "Asia", "Asia occidental"),
    "LB": ("Líbano", "Asia", "Asia occidental"),
    "JO": ("Jordania", "Asia", "Asia occidental"),
    "IN": ("India", "Asia", "Asia meridional"),
    "TH": ("Tailandia", "Asia", "Sudeste asiático"),
    "ID": ("Indonesia", "Asia", "Sudeste asiático"),
    "MY": ("Malasia", "Asia", "Sudeste asiático"),
    "JP": ("Japón", "Asia", "Asia oriental"),
    "CN": ("China", "Asia", "Asia oriental"),
    "KR": ("Corea del Sur", "Asia", "Asia oriental"),
    "AU": ("Australia", "Oceanía", "Oceanía"),
    "NZ": ("Nueva Zelanda", "Oceanía", "Oceanía"),
    "US": ("Estados Unidos", "América del Norte", "América del Norte"),
    "CA": ("Canadá", "América del Norte", "América del Norte"),
    "MX": ("México", "América del Norte", "América del Norte"),
    "BR": ("Brasil", "América del Sur", "América del Sur"),
    "AR": ("Argentina", "América del Sur", "América del Sur"),
    "CL": ("Chile", "América del Sur", "América del Sur"),
    "CO": ("Colombia", "América del Sur", "América del Sur"),
    "PE": ("Perú", "América del Sur", "América del Sur"),
    "UY": ("Uruguay", "América del Sur", "América del Sur"),
    "ZA": ("Sudáfrica", "África", "África austral"),
    "EG": ("Egipto", "África", "África septentrional"),
    "MA": ("Marruecos", "África", "África septentrional"),
}


def _cargar_cache() -> dict:
    if GEO_CACHE.exists():
        try:
            return json.load(open(GEO_CACHE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _guardar_cache(c: dict) -> None:
    try:
        json.dump(c, open(GEO_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass


def geocodificar(lugar: str):
    """Devuelve (pais, continente, subcontinente) reales o None."""
    global _last
    if not lugar or (lugar or "").strip().lower() in ("n/a", "na", ""):
        return None
    cache = _cargar_cache()
    q = lugar.replace("\n", " ").strip()
    if q in cache:
        return tuple(cache[q]) if cache[q] else None
    # throttle ~1.1s para respetar política de Nominatim
    ahora = time.time()
    falta = 1.1 - (ahora - _last)
    if falta > 0:
        time.sleep(falta)
    _last = time.time()
    try:
        import requests
        r = requests.get(NOM_URL, params={"q": q, "format": "json",
                        "limit": 1, "addressdetails": 1},
                        headers={"User-Agent": _UA}, proxies=PROXIES, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data:
                cc = (data[0].get("address", {}).get("country_code") or "").upper()
                info = COUNTRY_INFO.get(cc)
                if info:
                    cache[q] = list(info)
                    _guardar_cache(cache)
                    return info
                pais = data[0].get("address", {}).get("country", q)
                cache[q] = [pais, "", ""]
                _guardar_cache(cache)
                return (pais, "", "")
    except Exception:
        pass
    cache[q] = None
    _guardar_cache(cache)
    return None
