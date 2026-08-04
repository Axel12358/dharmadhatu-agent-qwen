from .scraper_publico import scrape_instagram_publico

# Reexportación aditiva del extractor de eventos de Instagram.
# No modifica ni deshabilita nada existente (suma, nunca resta).
try:
    from ..instagram_scraper import (
        scrape_instagram_events,
        scrape_instagram_events as scrape_instagram,
    )
except Exception:
    pass
