#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paquete de scraping de Telegram (Dharmadhatu Bot v5) — SIN API token.

Expone `scrape_telegram_events()`: búsqueda de canales públicos via
DuckDuckGo (site:t.me/s/), scraping de la vista web t.me/s/<canal> con
requests+BeautifulSoup, y extracción de eventos con EventExtractor.

Archivos de estado (dentro de este paquete, subcarpeta local del bot):
  - telegram_canales_estado.json      canales descubiertos/estado
  - telegram_canales_productivos.json canales con eventos (loop de mejora)
  - telegram_rendimiento.json         métricas por canal
"""

from .scraper import (
    scrape_telegram_events,
    CANALES_ESTADO,
    CANALES_PRODUCTIVOS,
    RENDIMIENTO,
    TIMEOUT_FASE,
)

__all__ = [
    "scrape_telegram_events",
    "CANALES_ESTADO",
    "CANALES_PRODUCTIVOS",
    "RENDIMIENTO",
    "TIMEOUT_FASE",
]
