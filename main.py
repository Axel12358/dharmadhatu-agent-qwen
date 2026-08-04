#!/usr/bin/env python3
"""
Dharmadhatu Bot v5 - Orquestador principal con Facebook integrado.
"""

import asyncio
from scrapers import scrape_goabase, scrape_songkick
from scrapers import scrape_ra
from scrapers.facebook_mcp import scrape_facebook_events
from utils.helpers import save_csv

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
    
    # Guardar resultados
    if eventos:
        save_csv(eventos, "eventos_encontrados.csv")
        print(f"\n📊 Total eventos consolidados: {len(eventos)}")
    else:
        print("\n⚠️ No se encontraron eventos en ninguna fuente")

if __name__ == "__main__":
    asyncio.run(main())
