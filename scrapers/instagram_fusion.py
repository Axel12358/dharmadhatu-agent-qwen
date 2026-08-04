#!/usr/bin/env python3
"""
Fusion Instagram Scraper — Combina TODAS las estrategias open-source SIN LOGIN.
Estrategias (ordenadas por velocidad → robustez):
  1. fast-tor-graphql  (do-me/fast-instagram-scraper: Torpy + GraphQL JSON)
  2. curl_cffi_tls     (curl-impersonate: TLS spoofing + requests)
  3. requests_stealth  (requests + headers rotados + proxy opcional)
  4. playwright_stealth (Playwright + stealth + mobile viewport)
  5. serp_fallback     (site:instagram.com via Yahoo/Startpage/DDG)
  6. instagramy_lib    (instagramy package: hashtag posts sin login)

Filosofía: cada estrategia es independiente, aislada en su propio proceso/thread.
Si una falla/bloquea, las demás continúan. Resultados se deduplican al final.
"""

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from scrapers.anti_block import get_anti_block
except Exception:
    get_anti_block = None

try:
    from scrapers.llm_local import extract_events_llm
except Exception:
    extract_events_llm = None

# ============================================================
# UTILIDADES COMUNES
# ============================================================

def _limpiar_hashtag(raw: str) -> str:
    """Normaliza hashtag: alnum, minúsculas, sin #/@/espacios."""
    return re.sub(r"[^a-z0-9]+", "", (raw or "").lower())

def _dedup_eventos(eventos: List[Dict]) -> List[Dict]:
    """Dedup por nombre+fecha+ciudad (case-insensitive)."""
    vistos = set()
    out = []
    for ev in eventos:
        key = (
            (ev.get("nombre", "").strip().lower()),
            (ev.get("fecha", "").strip()),
            (ev.get("ciudad", "").strip().lower()),
        )
        if key not in vistos:
            vistos.add(key)
            out.append(ev)
    return out

def _extraer_eventos_caption(caption: str, url: str, hashtag: str) -> List[Dict]:
    """Extrae eventos de un caption usando LLM local + regex fallback."""
    eventos = []
    if extract_events_llm:
        try:
            llm_evs = extract_events_llm(caption[:3000], url)
            for ev in llm_evs:
                ev.setdefault("hashtag_origen", hashtag)
                ev.setdefault("fuente", "instagram_fusion")
            eventos.extend(llm_evs)
        except Exception:
            pass
    # Fallback regex simple
    if not eventos:
        from scrapers.event_extractor import EventExtractor
        if EventExtractor:
            ext = EventExtractor()
            evs = ext.extract_all(caption, "Instagram", url) or []
            for ev in evs:
                ev.setdefault("hashtag_origen", hashtag)
                ev.setdefault("fuente", "instagram_fusion")
            eventos.extend(evs)
    return eventos

# ============================================================
# ESTRATEGIA 1: fast-tor-graphql (do-me/fast-instagram-scraper)
# ============================================================

def _estrategia_fast_tor_graphql(hashtag: str, max_posts: int = 30) -> List[Dict]:
    """
    Usa el script fast-instagram-scraper.py vía subprocess.
    Requiere: pip install torpy + repo clonado en /opt/fast-instagram-scraper o similar.
    """
    eventos = []
    script_path = os.getenv("FAST_IG_SCRIPT", "/opt/fast-instagram-scraper/fast-instagram-scraper.py")
    if not Path(script_path).exists():
        # Buscar en ubicaciones comunes
        for p in [
            "/tmp/fast-instagram-scraper/fast-instagram-scraper.py",
            str(Path.home() / "fast-instagram-scraper/fast-instagram-scraper.py"),
            "/opt/fast-instagram-scraper/fast-instagram-scraper.py",
        ]:
            if Path(p).exists():
                script_path = p
                break
        else:
            return []

    try:
        # Ejecutar: python script.py <hashtag> hashtag --max_posts N --save_as json
        cmd = [
            sys.executable, script_path,
            hashtag, "hashtag",
            "--max_posts", str(max_posts),
            "--save_as", "json",
            "--threads", "2",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=Path(script_path).parent)
        if result.returncode != 0:
            return []

        # Buscar archivo JSON generado (patrón: hashtag_*.json)
        out_dir = Path(script_path).parent
        json_files = list(out_dir.glob(f"{hashtag}_*.json")) + list(out_dir.glob("*.json"))
        for jf in json_files:
            try:
                with open(jf) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for post in data:
                        caption = post.get("caption", "") or post.get("accessibility_caption", "")
                        url = post.get("post_url") or post.get("url") or f"https://instagram.com/p/{post.get('shortcode','')}"
                        if caption:
                            eventos.extend(_extraer_eventos_caption(caption, url, hashtag))
            except Exception:
                continue
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return eventos

# ============================================================
# ESTRATEGIA 2: curl_cffi_tls (TLS spoofing Chrome)
# ============================================================

def _estrategia_curl_cffi_tls(hashtag: str, max_posts: int = 20, use_tor: bool = False) -> List[Dict]:
    """Usa curl_cffi para spoofear TLS Chrome + parsear HTML/JSON."""
    eventos = []
    try:
        from curl_cffi import requests
    except ImportError:
        return []

    url = f"https://www.instagram.com/explore/tags/{hashtag}/"
    try:
        # Impersonate Chrome 120 (TLS + HTTP/2 + headers realistas)
        r = requests.get(
            url,
            impersonate="chrome120",
            timeout=20,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            }
        )
        if r.status_code != 200 or "login" in r.url:
            return []

        # Parsear HTML para extraer posts (igual que _parse_html_posts)
        from scrapers.instagram_scraper import _parse_html_posts
        posts = _parse_html_posts(r.text)
        for post in posts[:max_posts]:
            caption = post.get("caption", "")
            post_url = post.get("url", "")
            if caption:
                eventos.extend(_extraer_eventos_caption(caption, post_url, hashtag))
    except Exception:
        pass
    return eventos

# ============================================================
# ESTRATEGIA 3: requests_stealth (requests + headers rotados + proxy)
# ============================================================

def _estrategia_requests_stealth(hashtag: str, max_posts: int = 20, use_tor: bool = True) -> List[Dict]:
    """Requests con anti-block headers + proxy Tor opcional."""
    eventos = []
    if not get_anti_block:
        return []

    ab = get_anti_block()
    if use_tor:
        ab._ensure_tor()
    proxies = ab.requests_proxies() if use_tor else None

    url = f"https://www.instagram.com/explore/tags/{hashtag}/"
    try:
        import requests
        headers = {
            "User-Agent": ab.random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        r = requests.get(url, headers=headers, timeout=20, proxies=proxies, allow_redirects=True)
        if r.status_code != 200 or "login" in r.url or len(r.text) < 2000:
            return []

        from scrapers.instagram_scraper import _parse_html_posts
        posts = _parse_html_posts(r.text)
        for post in posts[:max_posts]:
            caption = post.get("caption", "")
            post_url = post.get("url", "")
            if caption:
                eventos.extend(_extraer_eventos_caption(caption, post_url, hashtag))
    except Exception:
        pass
    return eventos

# ============================================================
# ESTRATEGIA 4: playwright_stealth (Playwright + stealth + mobile)
# ============================================================

async def _estrategia_playwright_stealth(hashtag: str, max_posts: int = 15, use_tor: bool = True) -> List[Dict]:
    """Playwright con stealth completo + mobile viewport + Tor."""
    eventos = []
    if not get_anti_block:
        return []

    ab = get_anti_block()
    if use_tor:
        ab._ensure_tor()

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
            )
            # Contexto mobile iPhone + stealth + Tor proxy
            context = await ab.create_stealth_context(browser, use_tor=use_tor)
            # Override viewport a mobile
            await context.set_viewport_size({"width": 390, "height": 844})
            
            page = await context.new_page()
            url = f"https://www.instagram.com/explore/tags/{hashtag}/"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # Scroll para cargar más posts
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(random.randint(800, 1500))
            
            content = await page.content()
            await browser.close()

            from scrapers.instagram_scraper import _parse_html_posts
            posts = _parse_html_posts(content)
            for post in posts[:max_posts]:
                caption = post.get("caption", "")
                post_url = post.get("url", "")
                if caption:
                    eventos.extend(_extraer_eventos_caption(caption, post_url, hashtag))
    except Exception:
        pass
    return eventos

# ============================================================
# ESTRATEGIA 5: serp_fallback (site:instagram.com via buscadores)
# ============================================================

def _estrategia_serp_fallback(hashtag: str, max_results: int = 10) -> List[Dict]:
    """Busca site:instagram.com #{hashtag} en Yahoo/Startpage/DDG."""
    eventos = []
    if not get_anti_block:
        return []

    ab = get_anti_block()
    queries = [
        f'site:instagram.com "#{hashtag}" psytrance party',
        f'site:instagram.com "#{hashtag}" festival',
        f'site:instagram.com "#{hashtag}" event',
    ]

    for query in queries:
        try:
            # Usar el buscador de facebook_public_events (ya implementado)
            from scrapers.facebook_public_events import _buscar_startpage, _buscar_yahoo
            for buscar_fn in [_buscar_startpage, _buscar_yahoo]:
                estado, bloques = asyncio.run(buscar_fn(query, None))  # context=None para solo requests
                if estado == "OK" and bloques:
                    for b in bloques[:3]:
                        caption = b.get("snippet", "") or b.get("title", "")
                        url = b.get("url", "")
                        if caption and "instagram.com" in url:
                            eventos.extend(_extraer_eventos_caption(caption, url, hashtag))
        except Exception:
            continue
    return eventos[:max_results]

# ============================================================
# ESTRATEGIA 6: instagramy_lib (package instagramy)
# ============================================================

def _estrategia_instagramy_lib(hashtag: str, max_posts: int = 20, use_tor: bool = False) -> List[Dict]:
    """Usa package instagramy (sin login, rate limited)."""
    eventos = []
    try:
        from instagramy import InstagramHashtag
    except ImportError:
        return []

    try:
        tag = InstagramHashtag(hashtag)  # sin sessionid
        urls = tag.posts_display_urls[:max_posts]
        for url in urls:
            # instagramy no da captions directos, solo URLs
            # Haríamos request individual a cada post (lento, rate limited)
            # Por ahora solo retornamos URLs como eventos mínimos
            eventos.append({
                "nombre": f"Post #{hashtag}",
                "fecha": "N/A",
                "lugar": "N/A",
                "ciudad": "N/A",
                "pais": "N/A",
                "subgenero": "",
                "organizador": "N/A",
                "url": url,
                "descripcion": f"Post de #{hashtag} via instagramy",
                "confidence": 0.3,
                "source_strategy": "instagramy_lib",
                "fuente": "instagram_fusion",
                "hashtag_origen": hashtag,
            })
    except Exception:
        pass
    return eventos

# ============================================================
# ORQUESTADOR FUSIÓN
# ============================================================

ESTRATEGIAS_DISPONIBLES = {
    "fast_tor_graphql": {
        "fn": _estrategia_fast_tor_graphql,
        "nombre": "Fast Tor GraphQL (do-me)",
        "rapida": True,
        "requiere_tor": True,
        "requiere_repo": True,
    },
    "curl_cffi_tls": {
        "fn": _estrategia_curl_cffi_tls,
        "nombre": "curl_cffi TLS Spoofing",
        "rapida": True,
        "requiere_tor": False,
    },
    "requests_stealth": {
        "fn": _estrategia_requests_stealth,
        "nombre": "Requests Stealth + Tor",
        "rapida": True,
        "requiere_tor": True,
    },
    "playwright_stealth": {
        "fn": _estrategia_playwright_stealth,
        "nombre": "Playwright Stealth Mobile",
        "rapida": False,
        "requiere_tor": True,
        "async": True,
    },
    "serp_fallback": {
        "fn": _estrategia_serp_fallback,
        "nombre": "SERP Fallback (site:instagram.com)",
        "rapida": False,
        "requiere_tor": False,
    },
    "instagramy_lib": {
        "fn": _estrategia_instagramy_lib,
        "nombre": "instagramy Library",
        "rapida": True,
        "requiere_tor": False,
    },
}

def scrape_instagram_fusion(
    hashtags: List[str],
    max_posts_per_tag: int = 20,
    use_tor: bool = True,
    estrategias_habilitadas: Optional[List[str]] = None,
    parallel: bool = False,  # Cambiado a False por defecto por issues de threading
    max_workers: int = 3,
) -> List[Dict]:
    """
    Fusion scraper principal.
    
    Args:
        hashtags: Lista de hashtags (sin #)
        max_posts_per_tag: Máx posts por hashtag por estrategia
        use_tor: Usar Tor donde la estrategia lo soporte
        estrategias_habilitadas: Lista de keys de ESTRATEGIAS_DISPONIBLES (None = todas)
        parallel: Ejecutar estrategias en paralelo (ThreadPoolExecutor)
        max_workers: Workers máximos en paralelo
    
    Returns:
        Lista de eventos deduplicados
    """
    if estrategias_habilitadas is None:
        estrategias_habilitadas = list(ESTRATEGIAS_DISPONIBLES.keys())
    
    # Filtrar estrategias que requieren repo externo si no está disponible
    estrategias_validas = {}
    for key in estrategias_habilitadas:
        if key not in ESTRATEGIAS_DISPONIBLES:
            continue
        cfg = ESTRATEGIAS_DISPONIBLES[key]
        if cfg.get("requiere_repo") and not Path(os.getenv("FAST_IG_SCRIPT", "/opt/fast-instagram-scraper/fast-instagram-scraper.py")).exists():
            print(f"   ⚠️ {cfg['nombre']}: repo no encontrado, saltando")
            continue
        estrategias_validas[key] = cfg

    print(f"🔀 FUSIÓN INSTAGRAM: {len(estrategias_validas)} estrategias × {len(hashtags)} hashtags")
    for key, cfg in estrategias_validas.items():
        print(f"   - {cfg['nombre']} ({'rápida' if cfg['rapida'] else 'lenta'})")

    todos_eventos = []
    t0 = time.time()

    def _ejecutar_estrategia_hashtag(args):
        key, hashtag = args
        cfg = estrategias_validas[key]
        fn = cfg["fn"]
        t_start = time.time()
        try:
            if cfg.get("async"):
                evs = asyncio.run(fn(hashtag, max_posts_per_tag, use_tor))
            else:
                evs = fn(hashtag, max_posts_per_tag, use_tor)
            dur = time.time() - t_start
            print(f"      ✅ {cfg['nombre']} #{hashtag}: {len(evs)} eventos ({dur:.1f}s)")
            return evs
        except Exception as e:
            dur = time.time() - t_start
            print(f"      ❌ {cfg['nombre']} #{hashtag}: {type(e).__name__} ({dur:.1f}s)")
            return []

    # Preparar tareas: (estrategia, hashtag)
    tareas = [(key, h) for key in estrategias_validas for h in hashtags]

    if parallel and len(tareas) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_ejecutar_estrategia_hashtag, t): t for t in tareas}
            for future in as_completed(futures):
                evs = future.result()
                todos_eventos.extend(evs)
    else:
        for tarea in tareas:
            evs = _ejecutar_estrategia_hashtag(tarea)
            todos_eventos.extend(evs)

    # Dedup final
    eventos_unicos = _dedup_eventos(todos_eventos)
    print(f"🔀 FUSIÓN completada: {len(todos_eventos)} raw → {len(eventos_unicos)} únicos ({time.time()-t0:.1f}s)")
    return eventos_unicos


# ============================================================
# ENTRY POINT COMPATIBLE CON improvement_loop
# ============================================================

def scrape_instagram_events(config: Optional[dict] = None) -> List[Dict]:
    """
    Entry point compatible: usa Fusion Scraper.
    Config keys:
        - hashtags: List[str] (opcional, si no se genera desde config_grupos)
        - max_hashtags: int (default 20)
        - max_posts_per_tag: int (default 20)
        - use_tor: bool (default True)
        - estrategias: List[str] (opcional)
        - parallel: bool (default True)
        - max_workers: int (default 3)
    """
    if config is None:
        config = {}

    # Generar hashtags si no vienen en config
    hashtags = config.get("hashtags")
    if not hashtags:
        from scrapers.instagram_scraper import _build_hashtags, _load_config
        cfg = _load_config()
        hashtags = _build_hashtags(cfg)[:config.get("max_hashtags", 20)]

    return scrape_instagram_fusion(
        hashtags=hashtags,
        max_posts_per_tag=config.get("max_posts_per_tag", 20),
        use_tor=config.get("use_tor", True),
        estrategias_habilitadas=config.get("estrategias"),
        parallel=config.get("parallel", True),
        max_workers=config.get("max_workers", 3),
    )


if __name__ == "__main__":
    # Test rápido
    test_tags = ["psytrance", "darkpsy"]
    eventos = scrape_instagram_fusion(
        hashtags=test_tags,
        max_posts_per_tag=5,
        use_tor=False,
        estrategias_habilitadas=["curl_cffi_tls", "requests_stealth", "instagramy_lib"],
        parallel=True,
        max_workers=2,
    )
    print(f"\n📊 Total eventos: {len(eventos)}")
    for ev in eventos[:5]:
        print(f"  - {ev.get('nombre','?')[:50]} | {ev.get('fuente','?')} | {ev.get('source_strategy','?')}")