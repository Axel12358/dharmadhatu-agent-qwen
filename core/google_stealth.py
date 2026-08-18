"""Google stealth utilities: persistent context, human behavior, Tor integration.

Reusable across all 3 dorks scrapers. Only uses libraries already installed.
Implements: persistent context, dual stealth, human behavior, gbv=1, cookies,
Tor rotation before each dork, CAPTCHA detection with retry, requests fallback.

Hard rule: ALL requests go through Tor (socks5h://127.0.0.1:<port>). Real IP never used.
"""
import asyncio
import json
import os
import random
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from bs4 import BeautifulSoup

from core.tor_pool import (
    IDENTIDADES_TOR,
    obtener_sesion_tor,
    verificar_tor,
    rotar_tor,
    obtener_siguiente_identidad,
    marcar_circuito_bloqueado,
)

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tor_data"
)
_PROFILE_DIR = os.path.join(_DATA_DIR, "pw_profile_google")
_PROFILE_DIR_FIREFOX = os.path.join(_DATA_DIR, "pw_profile_firefox")
_COOKIES_FILE = os.path.join(_DATA_DIR, "google_cookies.json")

_USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Chrome Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Firefox Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    # Firefox Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    # Edge Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]

_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}, loadTimes: function(){}, csi: function(){}};
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const p = [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
            {name: 'Native Client', filename: 'internal-nacl-plugin'},
        ];
        p.length = 3;
        return p;
    }
});
Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
Object.defineProperty(navigator, 'productSub', {get: () => '20030107'});
Object.defineProperty(navigator, 'vendor', {get: () => 'Google Inc.'});
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : originalQuery(parameters);
"""

# ---------------------------------------------------------------------------
# Cookie pools for rotation
# ---------------------------------------------------------------------------
_CONSENT_VALUES = [
    "YES+cb.20220301-11-p0.en+FX+700",
    "YES+cb.20220401-11-p0.en+FX+700",
    "YES+cb.20230101-11-p0.en+FX+700",
    "YES+cb.20230601-11-p0.en+FX+700",
    "YES+cb.20240101-11-p0.en+FX+700",
    "YES+cb.20240601-11-p0.en+FX+700",
    "YES+1",
    "YES+GB",
]

_SOCS_VALUES = [
    "CAISHAgCEhJnd3NfMjAyNDA0MTUtMF9SQzIaAmtvIAEaBgiA_LG2Bg",
    "CAISHAgCEhJnd3NfMjAyNDA1MTUtMF9SQzIaAmtvIAEaBgiA_LG2Bg",
    "CAISHAgCEhJnd3NfMjAyNDA2MTUtMF9SQzIaAmtvIAEaBgiA_LG2Bg",
    "CAI",
    "CAISNQoCGgAQAKgB",
    "CAISNgCGgAQAKgB",
]

_NID_VALUES = [
    "23=AVolaRzUJv0KJ5G2E1OjJU_YlGd8H2hDwJz1h6kQW5x8V1Z2m3n4o5p6q7r8s9t",
    "23=ABolaRzUJv0KJ5G2E1OjJU_YlGd8H2hDwJz1h6kQW5x8V1Z2m3n4o5p6q7r8s9t",
    "23=AColaRzUJv0KJ5G2E1OjJU_YlGd8H2hDwJz1h6kQW5x8V1Z2m3n4o5p6q7r8s9t",
    "23=ADolaRzUJv0KJ5G2E1OjJU_YlGd8H2hDwJz1h6kQW5x8V1Z2m3n4o5p6q7r8s9t",
    "23=AEolaRzUJv0KJ5G2E1OjJU_YlGd8H2hDwJz1h6kQW5x8V1Z2m3n4o5p6q7r8s9t",
]


def get_tor_port(indice_tor: Optional[int] = None) -> int:
    """Return a SOCKS port from the Tor pool that is actually listening.

    Never returns a port that is down. Falls back to the first listening
    port; if none listen, tries to start the pool; else returns 9050
    (the caller is expected to verify before use).
    """
    if indice_tor is not None and 0 <= indice_tor < len(IDENTIDADES_TOR):
        port = IDENTIDADES_TOR[indice_tor][0]
        if _tor_puerto_activo(port):
            return port

    for port, _control in IDENTIDADES_TOR:
        if _tor_puerto_activo(port):
            return port

    try:
        from core.tor_pool import iniciar_pool
        iniciar_pool()
        for port, _control in IDENTIDADES_TOR:
            if _tor_puerto_activo(port):
                return port
    except Exception:
        pass

    return 9050


def get_ua() -> str:
    """Return a rotating User-Agent from the pool."""
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Dynamic configuration (written by core/loop_mejora_dorks.py)
# ---------------------------------------------------------------------------
_CONFIG_LOOP_FILE = os.path.join(_DATA_DIR, "dorks_config_loop.json")

_DEFAULT_CONFIG = {
    "pausa_entre_dorks": [30, 60],
    "pausa_entre_reintentos": [45, 90],
    "max_dorks": 10,
    "cookies_file": os.path.join(_DATA_DIR, "google_cookies.json"),
    "rotar_cookies_cada": 1,
    "motores": ["google_playwright"],
    "timeout_total": 180,
    "max_reintentos": 3,
}


def load_loop_config() -> Dict[str, Any]:
    """Load dynamic configuration from dorks_config_loop.json.

    If file doesn't exist or is invalid, returns default config.
    Scrapers MUST call this before each execution to get current parameters.
    """
    if os.path.exists(_CONFIG_LOOP_FILE):
        try:
            with open(_CONFIG_LOOP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Merge with defaults to ensure all keys exist
                    cfg = dict(_DEFAULT_CONFIG)
                    cfg.update(data)
                    return cfg
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)


def save_loop_config(config: Dict[str, Any]) -> None:
    """Save dynamic configuration to dorks_config_loop.json."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _CONFIG_LOOP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _CONFIG_LOOP_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cookie management
# ---------------------------------------------------------------------------
def load_google_cookies() -> List[dict]:
    """Load cookies from google_cookies.json if exists.

    Expected format: list of dicts with name, value, domain, path.
    """
    if os.path.exists(_COOKIES_FILE):
        try:
            with open(_COOKIES_FILE) as f:
                data = json.load(f)
                if isinstance(data, list):
                    valid = [c for c in data
                             if isinstance(c, dict) and "name" in c and "value" in c]
                    if valid:
                        return valid
        except Exception:
            pass
    return []


def _save_google_cookies(cookies: List[dict]) -> None:
    """Persist cookies to google_cookies.json."""
    try:
        os.makedirs(os.path.dirname(_COOKIES_FILE), exist_ok=True)
        tmp = _COOKIES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(cookies, f, indent=2)
        os.replace(tmp, _COOKIES_FILE)
    except Exception:
        pass


def _build_random_cookies() -> List[dict]:
    """Build a random set of cookies from the pools (CONSENT, SOCS, NID).

    Each rotation picks different values from the pool, making the
    cookie fingerprint appear as a new visitor.
    """
    return [
        {
            "name": "CONSENT",
            "value": random.choice(_CONSENT_VALUES),
            "domain": ".google.com",
            "path": "/",
        },
        {
            "name": "SOCS",
            "value": random.choice(_SOCS_VALUES),
            "domain": ".google.com",
            "path": "/",
        },
        {
            "name": "NID",
            "value": random.choice(_NID_VALUES),
            "domain": ".google.com",
            "path": "/",
        },
    ]


def _build_google_cookies() -> List[dict]:
    """Build cookie list from file if exists, otherwise random from pools."""
    saved = load_google_cookies()
    if saved:
        return saved
    return _build_random_cookies()


def _add_cookies_sync(context) -> None:
    """Add cookies to the persistent context using context.add_cookies()."""
    all_cookies = _build_google_cookies()
    try:
        context.add_cookies(all_cookies)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Stealth helpers
# ---------------------------------------------------------------------------
def _ensure_profile_dir() -> str:
    """Ensure the persistent profile directory exists."""
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    return _PROFILE_DIR


def _apply_stealth_sync(page) -> None:
    """Apply dual stealth: undetected-playwright + playwright-stealth."""
    try:
        from undetected_playwright import stealth_sync
        stealth_sync(page)
        return
    except Exception:
        pass
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(page)
    except Exception:
        pass


async def _apply_stealth_async(page) -> None:
    """Apply dual stealth async: undetected-playwright + playwright-stealth."""
    try:
        from undetected_playwright import stealth_async
        await stealth_async(page)
        return
    except Exception:
        pass
    try:
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(page)
    except Exception:
        pass


def _human_move_mouse(page, x: int, y: int, steps: int = 5) -> None:
    """Simulate human-like mouse movement to coordinates."""
    try:
        page.mouse.move(x, y, steps=steps)
    except Exception:
        pass


def _human_type(page, selector: str, text: str, delay_ms: tuple = (50, 150)) -> None:
    """Type text character by character with random delays (human-like)."""
    try:
        page.click(selector)
        time.sleep(random.uniform(0.1, 0.3))
        for char in text:
            page.keyboard.type(char, delay=random.uniform(*delay_ms))
            time.sleep(random.uniform(0.02, 0.08))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------
def _detect_block(text: str) -> Tuple[bool, str]:
    """Detect CAPTCHA/block signals. Returns (blocked, signal_type).

    signal_type: "captcha", "recaptcha", "sorry", "429", "403", or "".
    """
    low = text[:3000].lower()
    if "unusual traffic" in low or "our systems have detected" in low:
        return True, "captcha"
    if "recaptcha" in low or "verify you are human" in low:
        return True, "recaptcha"
    if "/sorry" in low or "/sorry/index" in low:
        return True, "sorry"
    return False, ""


def _detect_block_from_status(status_code: int) -> Tuple[bool, str]:
    """Detect block from HTTP status code."""
    if status_code == 429:
        return True, "429"
    if status_code == 403:
        return True, "403"
    if status_code == 202:
        return True, "202"
    return False, ""


def _build_search_url(dork: str) -> str:
    """Build Google search URL with required params."""
    from urllib.parse import quote_plus
    return (
        f"https://www.google.com/search?q={quote_plus(dork)}"
        f"&hl=en&num=10&gbv=1&filter=0&pws=0&tbs=qdr:w"
    )


# ---------------------------------------------------------------------------
# Tor rotation with cookie rotation
# ---------------------------------------------------------------------------
def _rotate_tor_circuit() -> Tuple[bool, int]:
    """Rotate Tor to next available circuit. Returns (success, circuit_index)."""
    try:
        if verificar_tor():
            idx = obtener_siguiente_identidad()
            success = rotar_tor(idx)
            return success, idx
    except Exception:
        pass
    return False, 0


def _rotate_tor_and_cookies() -> None:
    """Rotate Tor circuit AND generate new cookies from the pool.

    Persists the new cookie set to google_cookies.json for the next session.
    Does NOT delete user_data_dir (persistent profile is preserved).
    """
    _rotate_tor_circuit()
    # Generate new cookies from the pool and persist them
    new_cookies = _build_random_cookies()
    _save_google_cookies(new_cookies)
    time.sleep(random.uniform(2, 4))


# ---------------------------------------------------------------------------
# Playwright search
# ---------------------------------------------------------------------------
def google_search_playwright(
    dork: str,
    scraper_name: str,
    indice_tor: Optional[int] = None,
    timeout: int = 20,
) -> Optional[str]:
    """Google search via sync Playwright with persistent context + human behavior.

    - Persistent profile in tor_data/pw_profile_google
    - Tor proxy on current circuit port (socks5://)
    - Dual stealth (undetected-playwright + playwright-stealth)
    - Human behavior: visit homepage, move mouse, type query char by char
    - gbv=1, tbs=qdr:w for recent results
    - Cookies: CONSENT+SOCS+NID from file or random from pools
    Returns HTML or None.
    """
    from playwright.sync_api import sync_playwright

    socks_port = get_tor_port(indice_tor)
    ua = get_ua()
    profile_dir = _ensure_profile_dir()

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            profile_dir,
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            proxy={"server": f"socks5://127.0.0.1:{socks_port}"},
            user_agent=ua,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="Europe/Berlin",
            java_script_enabled=True,
        )
        html = None
        try:
            page = context.pages[0] if context.pages else context.new_page()
            _apply_stealth_sync(page)
            page.add_init_script(_INIT_SCRIPT)

            # Add cookies (CONSENT+SOCS+NID from file or random pools)
            _add_cookies_sync(context)

            # Step 1: Visit google.com homepage (human behavior)
            try:
                page.goto(
                    "https://www.google.com",
                    timeout=timeout * 1000,
                    wait_until="domcontentloaded",
                )
                time.sleep(random.uniform(2, 4))

                # Random mouse movements
                _human_move_mouse(page, random.randint(200, 600), random.randint(100, 400))
                time.sleep(random.uniform(0.3, 0.8))
                _human_move_mouse(page, random.randint(400, 800), random.randint(200, 500))
                time.sleep(random.uniform(0.2, 0.5))
            except Exception:
                pass

            # Step 2: Type query character by character in search box
            try:
                _human_type(page, 'textarea[name="q"], input[name="q"]', dork)
                time.sleep(random.uniform(0.5, 1.5))
                page.keyboard.press("Enter")
                time.sleep(random.uniform(3, 6))
            except Exception:
                # Fallback: navigate directly with full URL params
                try:
                    page.goto(_build_search_url(dork),
                              timeout=timeout * 1000, wait_until="domcontentloaded")
                    time.sleep(random.uniform(3, 6))
                except Exception:
                    pass

            # Step 3: Detect block
            try:
                body_text = page.evaluate("document.body.innerText")
                if body_text:
                    blocked, signal = _detect_block(body_text)
                    if blocked:
                        pass  # html stays None
                else:
                    html = page.content()
            except Exception:
                # Context may have been closed
                pass

            # Step 4: Get HTML if not blocked
            if html is None:
                try:
                    body_text = page.evaluate("document.body.innerText")
                    if body_text:
                        blocked, signal = _detect_block(body_text)
                        if blocked:
                            pass
                        else:
                            html = page.content()
                except Exception:
                    pass

            # Final attempt: just grab content
            if html is None:
                try:
                    html = page.content()
                except Exception:
                    pass

        except Exception as e:
            html = None
        finally:
            try:
                context.close()
            except Exception:
                pass

        return html


# ---------------------------------------------------------------------------
# Requests fallback (always through Tor, never real IP)
# ---------------------------------------------------------------------------
def _requests_fallback(
    dork: str,
    scraper_name: str,
    timeout: int = 15,
) -> Optional[str]:
    """Fallback Google search using requests through Tor proxy.

    All requests go through Tor (socks5h://127.0.0.1:<port>). Returns HTML or None.
    """
    session = obtener_sesion_tor()
    if session is None:
        return None

    try:
        url = _build_search_url(dork)
        cookies = {}
        for c in _build_google_cookies():
            cookies[c["name"]] = c["value"]

        r = session.get(url, timeout=timeout, cookies=cookies)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Main entry point with retry
# ---------------------------------------------------------------------------
def google_search_playwright_with_retry(
    dork: str,
    scraper_name: str,
    max_retries: int = 3,
    indice_tor: Optional[int] = None,
    timeout: int = 20,
) -> Optional[str]:
    """Google search with retry on block. Rotates Tor + cookies BEFORE each dork.

    Block handling:
    - 429/403: wait 60-90s, rotate Tor + cookies, retry
    - reCAPTCHA/sorry: wait 90-120s, rotate Tor + cookies, retry
    - Max 3 retries per dork
    - Between dorks: 30-60s pause (caller handles this)
    Returns HTML or None after all retries exhausted.
    """
    for intento in range(max_retries):
        # Rotate Tor + cookies BEFORE each attempt (except first)
        if intento > 0:
            _rotate_tor_and_cookies()

        html = google_search_playwright(dork, scraper_name, indice_tor, timeout)
        if html:
            # Check for blocks in returned HTML
            blocked, signal = _detect_block(html)
            if blocked:
                if signal in ("recaptcha", "sorry"):
                    # CAPTCHA: wait 90-120s
                    wait = random.uniform(90, 120)
                    print(f"    🛡️ CAPTCHA detectado, pausa {wait:.0f}s...")
                else:
                    # 429/403: wait 60-90s
                    wait = random.uniform(60, 90)
                    print(f"    ⏳ Bloqueado ({signal or 'unknown'}), pausa {wait:.0f}s...")
                time.sleep(wait)
                continue
            return html

        # Playwright failed entirely
        if intento < max_retries - 1:
            # Between retries: 45-90s
            wait = random.uniform(45, 90)
            print(f"    ⏳ Intento {intento+1} falló, pausa {wait:.0f}s antes de reintento...")
            time.sleep(wait)

    # All Playwright retries failed: try requests fallback through Tor
    fallback_html = _requests_fallback(dork, scraper_name, timeout)
    if fallback_html:
        return fallback_html

    return None


# ---------------------------------------------------------------------------
# Helpers de parsing de resultados orgánicos
# ---------------------------------------------------------------------------
def _limpiar_url_google(href: str) -> str:
    """Limpia URLs de Google que vengan con /url?q= usando unquote."""
    if href.startswith("/url?q="):
        m = re.search(r"/url\?q=([^&]+)", href)
        if m:
            return unquote(m.group(1))
    if href.startswith("/url?"):
        m = re.search(r"url=([^&]+)", href)
        if m:
            return unquote(m.group(1))
    if href.startswith("https://www.google.com/url?q="):
        m = re.search(r"/url\?q=([^&]+)", href)
        if m:
            return unquote(m.group(1))
    return href


def _extraer_resultados_google(html: str) -> List[Dict]:
    """Extrae resultados orgánicos de una SERP de Google.

    Returns lista de dicts: {url (limpia), titulo, snippet}.
    """
    resultados: List[Dict] = []
    if not html:
        return resultados
    soup = BeautifulSoup(html, "lxml")
    for div in soup.select("div.g"):
        a = div.find("a", href=True)
        if not a:
            continue
        href = _limpiar_url_google(a["href"])
        if not href.startswith("http"):
            continue
        h3 = div.find("h3")
        titulo = h3.get_text(strip=True) if h3 else a.get_text(strip=True)
        if not titulo:
            continue
        snippet_el = (div.find("span", class_="st")
                      or div.find("div", attrs={"data-sncf": True})
                      or div.find("div", class_="VwiC3b"))
        snippet = snippet_el.get_text(strip=True) if snippet_el else ""
        resultados.append({
            "url": href.split("?")[0].rstrip("/"),
            "titulo": titulo,
            "snippet": snippet,
            "motor": "google",
        })
    return resultados


def _tor_puerto_activo(socks_port: int) -> bool:
    """Verifica que el puerto SOCKS de Tor está escuchando."""
    try:
        socket.create_connection(("127.0.0.1", socks_port), timeout=2).close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Firefox + Playwright + Tor
# ---------------------------------------------------------------------------
def google_search_firefox(
    dork: str,
    scraper_name: str,
    indice_tor: Optional[int] = None,
    timeout: int = 15,
) -> List[Dict]:
    """Google search via Firefox + Playwright + Tor.

    - `socks_port` del pool Tor (no fijo 9050).
    - Verifica el puerto con socket.create_connection antes de lanzar.
    - `launch_persistent_context` con proxy socks5.
    - headless, locale en-US, viewport 1366x768, UA de Firefox realista.
    - Extrae resultados orgánicos (URL limpia, título, snippet).

    Returns lista de dicts o [] si bloqueado / sin resultados.
    """
    from playwright.sync_api import sync_playwright

    socks_port = get_tor_port(indice_tor)
    if not _tor_puerto_activo(socks_port):
        print(f"    ⚠️ Firefox: puerto Tor {socks_port} inactivo, salto al siguiente motor")
        return []

    ua = random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
        "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    ])
    profile_dir = os.path.join(_PROFILE_DIR_FIREFOX, f"circuito_{socks_port}")
    os.makedirs(profile_dir, exist_ok=True)

    try:
        with sync_playwright() as p:
            context = p.firefox.launch_persistent_context(
                profile_dir,
                headless=True,
                proxy={"server": f"socks5://127.0.0.1:{socks_port}"},
                locale="en-US",
                viewport={"width": 1366, "height": 768},
                user_agent=ua,
                java_script_enabled=True,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.add_init_script(_INIT_SCRIPT)
                _add_cookies_sync(context)

                search_url = (
                    f"https://www.google.com/search?q={dork}"
                    f"&hl=en&num=10&gbv=1&filter=0&pws=0&tbs=qdr:w"
                )
                page.goto(search_url, timeout=timeout * 1000,
                          wait_until="domcontentloaded")
                time.sleep(random.uniform(2, 4))

                page.mouse.move(random.randint(200, 600), random.randint(100, 400))
                time.sleep(random.uniform(0.3, 0.8))

                # Detectar bloqueo
                try:
                    body_text = page.evaluate("document.body.innerText")
                    if body_text and _detect_block(body_text)[0]:
                        return []
                except Exception:
                    pass

                html = page.content()
            finally:
                context.close()
            return _extraer_resultados_google(html)

    except Exception as e:
        print(f"    ⚠️ Firefox search error: {e}")
        return []


# ---------------------------------------------------------------------------
# SearxNG pública a través de Tor
# ---------------------------------------------------------------------------
def buscar_en_searxng(dork: str, timeout: int = 15) -> List[Dict]:
    """Google alternativa via SearxNG (searx.be) a través de Tor.

    Usa requests.Session con proxy Tor (socks5h://127.0.0.1:<puerto>).
    Consulta `?format=json` y extrae la clave `results` (url, title, content).

    Returns lista de dicts o [] si falla.
    """
    session = obtener_sesion_tor()
    if session is None:
        return []

    try:
        url = f"https://searx.be/search?q={dork}&format=json&language=en-US"
        r = session.get(url, timeout=timeout)
        if r.status_code == 200:
            try:
                data = r.json()
                results = data.get("results", [])
            except Exception:
                return []
            out: List[Dict] = []
            for item in results:
                url = item.get("url", "")
                title = item.get("title", "")
                content = item.get("content", "")
                if url and title:
                    out.append({
                        "url": url.split("?")[0].rstrip("/"),
                        "titulo": title,
                        "snippet": content or "",
                        "motor": "searxng",
                    })
            return out
    except Exception as e:
        print(f"    ⚠️ SearxNG error: {e}")

    return []


# ---------------------------------------------------------------------------
# Requests fallback a Google a través de Tor
# ---------------------------------------------------------------------------
def buscar_en_requests_fallback(
    dork: str,
    scraper_name: str,
    indice_tor: Optional[int] = None,
    timeout: int = 15,
) -> List[Dict]:
    """Fallback Google usando requests.Session con proxy Tor (socks5h).

    Extrae URLs, títulos y snippets con bs4. Limpia `/url?q=`.

    Returns lista de dicts o [] si falla.
    """
    socks_port = get_tor_port(indice_tor)
    session = obtener_sesion_tor(indice=indice_tor)
    if session is None:
        return []

    try:
        url = _build_search_url(dork)
        cookies = {}
        for c in _build_google_cookies():
            cookies[c["name"]] = c["value"]

        r = session.get(url, timeout=timeout, cookies=cookies)
        if r.status_code == 200:
            return _extraer_resultados_google(r.text)
    except Exception as e:
        print(f"    ⚠️ Requests fallback error: {e}")

    return []


# ---------------------------------------------------------------------------
# Intento priorizado: Firefox -> SearxNG -> requests fallback
# ---------------------------------------------------------------------------
def google_search_prioritario(
    dork: str,
    scraper_name: str,
    indice_tor: Optional[int] = None,
    timeout: int = 15,
) -> List[Dict]:
    """Búsqueda fusionada multi-motor a través de Tor.

    Prioridad: DuckDuckGo → Bing → Startpage → Mojeek → SearxNG → requests
    fallback a Google.

    Returns lista de dicts {url, titulo, snippet, motor} (primera colección
    no vacía si se quiere prioritario, pero fusionar_resultados ya fusiona
    todo y deduplica por URL).
    """
    from core.busqueda_fusionada import fusionar_resultados
    return fusionar_resultados(dork, indice_tor=indice_tor, timeout=timeout)


# ---------------------------------------------------------------------------
# Función auxiliar: detectar si Camoufox está disponible
# ---------------------------------------------------------------------------
def _camoufox_disponible() -> bool:
    """Check if Camoufox is available for import."""
    try:
        import camoufox  # noqa: F401
        return True
    except ImportError:
        return False
