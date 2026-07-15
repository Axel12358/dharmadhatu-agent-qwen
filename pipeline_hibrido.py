#!/usr/bin/env python3
"""
Pipeline híbrido de scraping + IA para eventos de psytrance
100% Python, sin Node.js ni JavaScript

Fuentes:
- Facebook: usa facebook-scraper (sin login)
- Instagram: usa instaloader (sin login, perfiles públicos)
- Enriquecimiento: Qwen Coder (vía Ollama)
"""

import time
import pandas as pd
import json
import re
import os
from datetime import datetime

# ============================================================
# CONFIGURACIÓN
# ============================================================
MAX_EVENTOS_POR_FUENTE = 15
HASHTAG_BUSQUEDA = "psytrance festival"
PERFIL_INSTAGRAM = "psytrance"

# ============================================================
# IMPORTS DE DEPENDENCIAS
# ============================================================
try:
    from scrapers.facebook_public import scrape_facebook_public
    FACEBOOK_AVAILABLE = True
except ImportError:
    FACEBOOK_AVAILABLE = False
    print("⚠️ scrapers.facebook_public no encontrado. Crear el archivo.")

try:
    from scrapers.instagram_public import scrape_instagram_public
    INSTAGRAM_AVAILABLE = True
except ImportError:
    INSTAGRAM_AVAILABLE = False
    print("⚠️ scrapers.instagram_public no encontrado. Crear el archivo.")

try:
    from scrapers.qwen_enricher import enriquecer_eventos
    QWEN_AVAILABLE = True
except ImportError:
    QWEN_AVAILABLE = False
    print("⚠️ scrapers.qwen_enricher no encontrado. Crear el archivo.")

# ============================================================
# PIPELINE PRINCIPAL
# ============================================================
def ejecutar_pipeline():
    """Ejecuta todo el pipeline híbrido"""
    
    print("="*60)
    print("🌀 PIPELINE HÍBRIDO - SCRAPING + IA")
    print("="*60)
    print(f"📌 FACEBOOK: {HASHTAG_BUSQUEDA}")
    print(f"📌 INSTAGRAM: @{PERFIL_INSTAGRAM}")
    print("="*60)
    
    eventos = []
    
    # 1. Scrapear Facebook
    print("\n📸 [1/3] Scrapeando Facebook...")
    if FACEBOOK_AVAILABLE:
        eventos_fb = scrape_facebook_public(HASHTAG_BUSQUEDA, MAX_EVENTOS_POR_FUENTE)
        eventos.extend(eventos_fb)
    else:
        print("❌ Facebook no disponible")
        eventos_fb = []
    
    # 2. Scrapear Instagram
    print("\n📸 [2/3] Scrapeando Instagram...")
    if INSTAGRAM_AVAILABLE:
        eventos_ig = scrape_instagram_public(PERFIL_INSTAGRAM, MAX_EVENTOS_POR_FUENTE)
        eventos.extend(eventos_ig)
    else:
        print("❌ Instagram no disponible")
        eventos_ig = []
    
    print(f"\n✅ Eventos totales (sin enriquecer): {len(eventos)}")
    
    # 3. Enriquecer con Qwen
    if QWEN_AVAILABLE and eventos:
        print("\n🤖 [3/3] Enriqueciendo con Qwen...")
        eventos = enriquecer_eventos(eventos)
    else:
        print("\n⚠️ [3/3] Qwen no disponible o sin eventos, omitiendo enriquecimiento")
    
    # 4. Consolidar
    if eventos:
        df = pd.DataFrame(eventos)
        df = df.drop_duplicates(subset=['nombre'], keep='first')
        os.makedirs('data', exist_ok=True)
        df.to_csv('data/events_hibridos.csv', index=False)
        
        print(f"\n✅ {len(df)} eventos guardados en data/events_hibridos.csv")
        print("\n📊 Distribución por fuente:")
        for fuente, count in df['fuente'].value_counts().items():
            print(f"   • {fuente}: {count}")
        
        print("\n📌 EJEMPLOS DE EVENTOS ENRIQUECIDOS:")
        for i, row in df.head(5).iterrows():
            print(f"   {i+1}. {row['nombre'][:50]}")
            print(f"      Organizador: {row['organizador']}")
            print(f"      Email: {row['email']}")
            print(f"      Fuente: {row['fuente']}\n")
    else:
        print("⚠️ No se encontraron eventos")
    
    print("="*60)
    print("🏆 PIPELINE COMPLETADO")
    print("="*60)
    
    return eventos

if __name__ == '__main__':
    ejecutar_pipeline()
