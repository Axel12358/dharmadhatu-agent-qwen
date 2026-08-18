#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probar_dorks.py — Runner con reintentos y rotación Tor.

Ejecuta los módulos de dorks en secuencia con hasta 3 reintentos cada uno,
rotando Tor entre intentos. Muestra tiempo, resultados y resumen final.

Principio: siempre sumar, nunca restar. Tor primero, IP real jamás.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# --- Tor ---
try:
    from core.tor_manager import tor_disponible
except ImportError:
    tor_disponible = lambda: False

try:
    from core.tor_utils import verificar_tor, rotar_tor
except ImportError:
    verificar_tor = None
    rotar_tor = None


MAX_RETRIES = 3


def _verificar_tor() -> bool:
    """Verifica Tor usando tor_utils si está disponible."""
    if verificar_tor is not None:
        try:
            return verificar_tor()
        except Exception:
            pass
    return bool(tor_disponible())


def _rotar_tor() -> bool:
    """Rota Tor usando tor_utils si está disponible."""
    if rotar_tor is not None:
        try:
            return rotar_tor()
        except Exception:
            pass
    return False


def _normalizar_resultados(data):
    """Normaliza el retorno de un scraper a (eventos, grupos, organizadores)."""
    if isinstance(data, dict):
        eventos = data.get("eventos", [])
        grupos = data.get("grupos", [])
        organizadores = data.get("organizadores", set())
        if isinstance(organizadores, list):
            organizadores = set(organizadores)
        return eventos, grupos, organizadores
    elif isinstance(data, list):
        # Algunos scrapers devuelven una lista de dicts
        eventos = [d for d in data if isinstance(d, dict)]
        return eventos, [], set()
    return [], [], set()


def _ejecutar_con_reintentos(scraper_name: str, func, limite_dorks: int = 5):
    """Ejecuta una función de scraper con hasta MAX_RETRIES intentos.
    Entre cada intento fallido, rota Tor."""
    start = time.time()
    ultima_excepcion = None

    for intento in range(1, MAX_RETRIES + 1):
        print(f"\n[{scraper_name}] Intento {intento}/{MAX_RETRIES} "
              f"(Tor: {'✓' if _verificar_tor() else '✗'})")
        try:
            data = func(limite=limite_dorks)
            elapsed = time.time() - start
            eventos, grupos, organizadores = _normalizar_resultados(data)
            total = len(eventos) + len(grupos) + len(organizadores)
            print(f"  ✅ Tiempo: {elapsed:.0f}s | Eventos: {len(eventos)} | "
                  f"Grupos: {len(grupos)} | "
                  f"Organizadores: {len(organizadores)}")
            if total > 0:
                return data, elapsed, intento
        except Exception as e:
            ultima_excepcion = e
            print(f"  ❌ Error: {str(e)[:80]}")
        if intento < MAX_RETRIES:
            print(f"  🔄 Rotando Tor para reintento...")
            rotado = _rotar_tor()
            print(f"  🔁 Tor rotado: {rotado}")

    elapsed = time.time() - start
    return {"eventos": [], "grupos": [], "organizadores": set()}, elapsed, MAX_RETRIES


def main():
    print("=" * 70)
    print(" Dharmadhatu Dorks Runner — con reintentos y rotación Tor")
    print("=" * 70)
    print(f"Tor verificado: {_verificar_tor()}")
    print(f"Máx reintentos por módulo: {MAX_RETRIES}")

    if not _verificar_tor():
        print("\n⚠️  Tor no está disponible. El scraping no se ejecutará.")
        print("   Inicia Tor en 127.0.0.1:9050 antes de continuar.")
        return

    # Importar scrapers (lazy, para no fallar si alguno falta)
    scrapers_info = []

    try:
        from scrapers.facebook_dorks import scrape_facebook_dorks
        scrapers_info.append(("Facebook Dorks", scrape_facebook_dorks))
        print("  + scrapers/facebook_dorks.py")
    except ImportError as e:
        print(f"  - scrapers/facebook_dorks.py: {e}")

    try:
        from scrapers.instagram_dorks import scrape_instagram_dorks
        scrapers_info.append(("Instagram Dorks", scrape_instagram_dorks))
        print("  + scrapers/instagram_dorks.py")
    except ImportError as e:
        print(f"  - scrapers/instagram_dorks.py: {e}")

    try:
        from scrapers.dorks_resultados import scrape_dorks_resultados
        scrapers_info.append(("Dorks Resultados", scrape_dorks_resultados))
        print("  + scrapers/dorks_resultados.py")
    except ImportError as e:
        print(f"  - scrapers/dorks_resultados.py: {e}")

    if not scrapers_info:
        print("\nNo se pudieron importar scrapers. Abortando.")
        return

    print("\n" + "-" * 70)
    print(" Iniciando ejecución de scrapers...")
    print("-" * 70)

    resumen_total = {
        "scrapers_ok": 0,
        "scrapers_fallidos": 0,
        "total_eventos": 0,
        "total_grupos": 0,
        "total_organizadores": 0,
        "tiempo_total": 0.0,
    }

    for nombre, func in scrapers_info:
        print(f"\n{'='*40}")
        print(f" Ejecutando: {nombre}")
        print(f"{'='*40}")
        data, elapsed, intento = _ejecutar_con_reintentos(nombre, func, limite_dorks=5)
        total = len(data.get("eventos", [])) + len(data.get("grupos", [])) + len(data.get("organizadores", set()))
        if total > 0:
            resumen_total["scrapers_ok"] += 1
            print(f"  ✅ {nombre}: ÉXITO en intento {intento}")
        else:
            resumen_total["scrapers_fallidos"] += 1
            print(f"  ❌ {nombre}: SIN HALLAZGOS")
        resumen_total["total_eventos"] += len(data.get("eventos", []))
        resumen_total["total_grupos"] += len(data.get("grupos", []))
        resumen_total["total_organizadores"] += len(data.get("organizadores", set()))
        resumen_total["tiempo_total"] += elapsed

    # Resumen final
    print(f"\n{'='*70}")
    print(" RESUMEN FINAL")
    print(f"{'='*70}")
    print(f"  Scrapers exitosos:  {resumen_total['scrapers_ok']}")
    print(f"  Scrapers fallidos:   {resumen_total['scrapers_fallidos']}")
    print(f"  Total eventos:       {resumen_total['total_eventos']}")
    print(f"  Total grupos:        {resumen_total['total_grupos']}")
    print(f"  Total organizadores: {resumen_total['total_organizadores']}")
    print(f"  Tiempo total:        {resumen_total['tiempo_total']:.0f}s")
    print(f"\nTor final verificado: {_verificar_tor()}")
    print("=" * 70)


if __name__ == "__main__":
    main()
