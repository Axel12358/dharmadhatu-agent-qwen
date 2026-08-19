#!/usr/bin/env python3
"""
Dharmadhatu Bot v5 - Orquestador principal con Facebook integrado.
"""

import asyncio
from scrapers import scrape_goabase, scrape_songkick
from scrapers import scrape_ra
from scrapers.facebook_mcp import scrape_facebook_events
from utils.helpers import save_csv
from scrapers.event_extractor import EventExtractor
from utils import geo_utils

# Fuentes donde "general" = probablemente NO es psytrance (reversible, configurable)
FUENTES_NO_PSY = {"Resident Advisor"}


def filtrar_no_psy(eventos):
    """Marca como 'no_psy' los eventos con subgenero='general' de fuentes no especializadas.

    Goabase es portal de psytrance → se excluye de esta lista.
    Configurable: añadir/quitar fuentes en FUENTES_NO_PSY.
    """
    for ev in eventos:
        if ev.get("subgenero") == "general" and ev.get("fuente") in FUENTES_NO_PSY:
            ev["subgenero"] = "no_psy"
    return eventos


def clasificar_eventos(eventos):
    """Añade/rellena la columna subgenero usando EventExtractor.clasificar_subgenero.

    Si el evento ya trae un subgénero distinto de "general" (p.ej. heredado de
    las keywords de búsqueda de Facebook), se respeta y no se sobrescribe.
    """
    extractor = EventExtractor()
    for ev in eventos:
        # Respetar subgénero ya asignado (p.ej. herencia desde keywords de FB)
        if ev.get("subgenero") and ev["subgenero"] != "general":
            continue
        # Obtener texto representativo: nombre + lugar + descripcion
        texto = " ".join(filter(None, [ev.get("nombre"), ev.get("lugar"), ev.get("descripcion")]))
        ev["subgenero"] = extractor.clasificar_subgenero(texto)
    return eventos


def clasificar_geo_events(eventos):
    """Añade continente y subcontinente a cada evento usando geo_utils.clasificar_geo."""
    for ev in eventos:
        pais = ev.get("pais")
        if pais and pais not in ("N/A", ""):
            geo = geo_utils.clasificar_geo(pais)
            ev.update(geo)
        else:
            ev.update({"continente": "Otro", "subcontinente": "Otro"})
    return eventos


async def main():
    print("🧘 Dharmadhatu Bot v5")
    print("=" * 50)
    
    eventos = []
    
    # 1. Goabase
    print("\n🌐 Scraping Goabase...")
    try:
        goabase_events = await asyncio.to_thread(scrape_goabase, limit=100)
        if goabase_events:
            print(f"   ✅ {len(goabase_events)} eventos")
            eventos.extend(goabase_events)
        else:
            print("   ⚠️ No se encontraron eventos en Goabase")
    except Exception as e:
        print(f"   ❌ Error en Goabase: {e}")
    
    # 2. Songkick
    print("\n🎵 Scraping Songkick...")
    try:
        songkick_events = await asyncio.to_thread(scrape_songkick, limit=100)
        if songkick_events:
            print(f"   ✅ {len(songkick_events)} eventos")
            eventos.extend(songkick_events)
        else:
            print("   ⚠️ No se encontraron eventos en Songkick")
    except Exception as e:
        print(f"   ❌ Error en Songkick: {e}")
    
    # 3. Facebook (todo junto: grupos + búsqueda pública SERP)
    print("\n📱 Scraping Facebook (grupos + búsqueda pública)...")
    try:
        facebook_events = await scrape_facebook_events()
        if facebook_events:
            print(f"   ✅ {len(facebook_events)} eventos encontrados")
            eventos.extend(facebook_events)
        else:
            print("   ⚠️ No se encontraron eventos en Facebook")
    except Exception as e:
        print(f"   ❌ Error en Facebook: {e}")
    
    # 4. Resident Advisor (GraphQL, sin login)
    print("\n🌐 Scraping Resident Advisor...")
    try:
        ra_events = await asyncio.to_thread(scrape_ra)
        if ra_events:
            print(f"   ✅ {len(ra_events)} eventos")
            eventos.extend(ra_events)
        else:
            print("   ⚠️ No se encontraron eventos en Resident Advisor")
    except Exception as e:
        print(f"   ❌ Error en Resident Advisor: {e}")
    
    # 5. Instagram (SUMA opcional — extrae eventos de hashtags organizadores conocidos).
    #    No modifica ni deshabilita scrapers existentes. Si el módulo o Instaloader
    #    fallan, el flujo continúa sin interrupción.
    #    Requiere pip install instaloader. Con instagram_session.json hay mayor yield.
    try:
        from scrapers.instagram import scrape_instagram_events
        print("\n📱 Scraping Instagram (hashtags + fallback) ...")
        ig_events = await asyncio.to_thread(scrape_instagram_events)
        if ig_events:
            print(f"   ✅ {len(ig_events)} eventos de Instagram")
            eventos.extend(ig_events)
        else:
            print("   ⚠️ Sin eventos de Instagram (requiere sesión para mayor yield)")
    except ImportError:
        print("\n📱 Instagram: scraper no disponible (instaloader ausente) — omitido")
    except Exception as e:
        print(f"   ❌ Error en Instagram (flujo continua): {e}")
    
    # Clasificar eventos por subgénero para enriquecimiento
    eventos = clasificar_eventos(eventos)
    eventos = filtrar_no_psy(eventos)
    eventos = clasificar_geo_events(eventos)

    # Guardar resultados
    if eventos:
        save_csv(eventos, "eventos_encontrados.csv")
        print(f"\n📊 Total eventos consolidados: {len(eventos)}")
    else:
        print("\n⚠️ No se encontraron eventos en ninguna fuente")

    # ---- Loop Central de Optimización (aditivo, no intrusivo) ----
    # Si core/orquestador.py existe, ejecuta la orquestación paralela con
    # dedup global y suma los eventos nuevos al CSV. Nunca borra datos.
    try:
        from pathlib import Path
        if Path(__file__).resolve().parent.joinpath("core", "orquestador.py").exists():
            print("\n🧠 Orquestando scrapers adicionales (dedup global)...")
            from core.orquestador import orquestar_scrapers
            nuevos = orquestar_scrapers()
            print(f"   ➕ {len(nuevos)} eventos nuevos añadidos por el orquestador")
    except Exception as e:
        print(f"   ⚠️ Orquestador no disponible: {type(e).__name__}: {e}")

    # ---- Completar N/A de Facebook (aditivo, no intrusivo) ----
    # Visita las URLs de eventos FB con campos N/A (fecha/lugar/organizador)
    # y los completa con Playwright. No pierde información: lo que no se puede
    # obtener se deja como estaba.
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "scrapers", "completar_fb_na.py").exists():
            print("\n🔧 Completando N/A de Facebook (Playwright)...")
            from scrapers.completar_fb_na import completar_eventos_fb
            _res = completar_eventos_fb()
            print(f"   ➕ {_res['actualizados']} eventos de Facebook completados")
    except Exception as e:
        print(f"   ⚠️ Completar FB N/A no disponible: {type(e).__name__}: {e}")

    # ---- Completar N/A de Facebook con búsqueda externa (aditivo, no intrusivo) ----
    # Para aquellos eventos FB donde Playwright no mostró lugar o fecha, buscamos
    # en DuckDuckGo/Google una página web alternativa (foros, sitios de entradas)
    # que contenga lugar o fecha claros.
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "scrapers", "completar_fb_externo.py").exists():
            print("\n🔎 Completando N/A de Facebook (búsqueda externa DuckDuckGo)...")
            from scrapers.completar_fb_externo import completar_fb_externo
            _res = completar_fb_externo()
            print(f"   ➕ {_res['actualizados']} eventos de Facebook completados externamente")
    except Exception as e:
        print(f"   ⚠️ Completar FB externo no disponible: {type(e).__name__}: {e}")

    # ---- Facebook Dorks (Google Dorks vía DuckDuckGo) — opcional y aditiva ----
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "scrapers", "facebook_dorks.py").exists():
            print("\n🔍 Facebook Dorks (búsqueda OSINT)...")
            from scrapers.facebook_dorks import scrape_facebook_dorks
            _evs_dorks = scrape_facebook_dorks()
            print(f"   ➕ {len(_evs_dorks)} eventos de Facebook Dorks")
    except Exception as e:
        print(f"   ⚠️ Facebook Dorks no disponible: {type(e).__name__}: {e}")

    # ---- Instagram Dorks (Google Dorks vía DuckDuckGo) — opcional y aditiva ----
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "scrapers", "instagram_dorks.py").exists():
            print("\n🔍 Instagram Dorks (búsqueda OSINT)...")
            from scrapers.instagram_dorks import scrape_instagram_dorks
            _evs_ig_dorks = scrape_instagram_dorks()
            print(f"   ➕ {len(_evs_ig_dorks)} eventos de Instagram Dorks")
    except Exception as e:
        print(f"   ⚠️ Instagram Dorks no disponible: {type(e).__name__}: {e}")

    # ---- MCP Organizador (motor central de dorks) — opcional y aditiva ----
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "core", "mcp_organizador.py").exists():
            print("\n🔧 MCP Organizador (motor central de dorks)...")
            from core.mcp_organizador import ejecutar_mcp_dorks
            _evs_mcp = ejecutar_mcp_dorks()
            print(f"   ➕ {len(_evs_mcp)} eventos de MCP Organizador")
    except Exception as e:
        print(f"   ⚠️ MCP Organizador no disponible: {type(e).__name__}: {e}")

    # ---- Agente Coordinador (Sistema Multiagente Ligero) — opcional y aditiva ----
    # Si core/agente_coordinador.py existe, ejecuta los subagentes en paralelo
    # (máx 3 simultáneos, semáforo Tor máx 2) y suma eventos nuevos al CSV.
    # Nunca rompe el flujo: si no está disponible, se sigue igual que antes.
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "core", "agente_coordinador.py").exists():
            print("\n🤖 Agente Coordinador (multiagente ligero)...")
            from core.agente_coordinador import ejecutar_agentes
            _nuevos_coord = ejecutar_agentes()
            print(f"   ➕ {_nuevos_coord} eventos nuevos añadidos por el coordinador de agentes")
    except Exception as e:
        print(f"   ⚠️ Agente Coordinador no disponible: {type(e).__name__}: {e}")

    # ---- Supervisión algebra lineal (opcional, aditiva) ----
    # Capa de mejora por álgebra lineal: rellena N/A (subgénero, tipo_lugar)
    # con confianza alta y deduplica duplicados internos por similitud. Nunca
    # resta eventos válidos; si el módulo no existe, se ignora sin romper.
    try:
        from pathlib import Path as _P
        if _P(__file__).resolve().parent.joinpath(
                "core", "algebra_lineal.py").exists():
            print("\n🧮 Supervisión algebra lineal (mejora CSV)...")
            from core.algebra_lineal import supervisar_y_mejorar
            _res_alg = supervisar_y_mejorar(
                str(_P(__file__).resolve().parent / "eventos_encontrados.csv"),
                aplicar_cambios=True)
            _rs = _res_alg.get("resumen", {})
            print(f"   ➕ {_rs.get('subgeneros_rellenados', 0)} subgéneros, "
                  f"{_rs.get('tipos_lugar_rellenados', 0)} tipos de lugar "
                  f"(dedup: {_rs.get('duplicados_eliminados', 0)})")
    except Exception as e:
        print(f"   ⚠️ Supervisión algebra no disponible: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
