# Aplicación de repos y mejoras anti-bloqueo — Guía de implementación

> Fecha: 2026-08-16 · Complemento de `herramientas_antibot_github.md`.
> Este documento especifica **CÓMO** aplicar cada repo/técnica a los 3 scrapers de dorks, con código concreto y por archivo.

## Archivos objetivo

| Archivo | Motor | Playwright API |
|---------|-------|----------------|
| `scrapers/dorks_resultados.py` | Google | **async** (`async_playwright`) |
| `scrapers/facebook_dorks.py` | Google | **async** (`async_playwright`) |
| `scrapers/instagram_dorks.py` | Google | **sync** (`sync_playwright`) |

Todos usan: pool de Tor (`core/tor_pool.py`), puerto SOCKS extraído de `session.proxies`, `_USER_AGENTS` rotativo, `playwright-stealth`, delays 7-18s, `num=10&filter=0&pws=0`.

---

## MEJORA 1 — Perfil persistente (`launch_persistent_context`)

**Fuente:** iwanghc/mcp_web_search. **Impacto:** ALTO (Google ve un browser establecido → menos CAPTCHAs).

**Qué cambiar:** reemplazar `browser.launch()` + `browser.new_context()` por `p.chromium.launch_persistent_context()`.

### 1a. `scrapers/dorks_resultados.py` — en `_google_search_playwright` (línea ~361)

Reemplazar el bloque desde `async def _run_real():`:

```python
        async def _run_real():
            async with async_playwright() as p:
                user_data_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tor_data", "pw_profile_dorks")
                os.makedirs(user_data_dir, exist_ok=True)
                proxy = {"server": f"socks5://127.0.0.1:{socks_port}"}
                context = await p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled",
                          "--disable-web-security",
                          "--disable-features=IsolateOrigins,site-per-process"],
                    proxy=proxy,
                    user_agent=ua,
                    viewport={"width": 1920, "height": 1080},
                    java_script_enabled=True,
                )
                page = context.pages[0] if context.pages else await context.new_page()
                if STEALTH_AVAILABLE and Stealth is not None:
                    try:
                        await Stealth().apply_stealth_async(page)
                    except Exception:
                        pass
                # ... (mantener pasos 1 y 2 de navegación tal cual) ...
                html = await page.content()
                await context.close()
                return html
```

**Importante:** `launch_persistent_context` devuelve un `context` que ya tiene la página abierta. No se llama a `browser.close()` sino a `context.close()`. Se mantiene el bloque de navegación (homepage + search) sin cambios.

### 1b. `scrapers/facebook_dorks.py` — en `_google_search_playwright` (línea ~318)

Mismo patrón, usando `pw_profile_fb` como directorio:

```python
        async def _run():
            async with async_playwright() as p:
                user_data_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "tor_data", "pw_profile_fb")
                os.makedirs(user_data_dir, exist_ok=True)
                proxy = {"server": f"socks5://127.0.0.1:{socks_port}"}
                context = await p.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled",
                          "--disable-web-security",
                          "--disable-features=IsolateOrigins,site-per-process",
                          "--no-first-run",
                          "--no-default-browser-check"],
                    proxy=proxy,
                    user_agent=ua,
                    viewport={"width": 1920, "height": 1080},
                    java_script_enabled=True,
                )
                page = context.pages[0] if context.pages else await context.new_page()
                # ... (stealth + navegación tal cual) ...
                html = await page.content()
                await context.close()
                return html
```

### 1c. `scrapers/instagram_dorks.py` — en `_google_search_playwright_ig` (línea ~838) — versión **sync**

```python
        with sync_playwright() as p:
            user_data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "tor_data", "pw_profile_ig")
            os.makedirs(user_data_dir, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                user_data_dir,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage",
                      "--disable-blink-features=AutomationControlled",
                      "--disable-web-security",
                      "--disable-features=IsolateOrigins,site-per-process",
                      "--no-first-run",
                      "--no-default-browser-check"],
                proxy={"server": f"socks5://127.0.0.1:{socks_port}"},
                user_agent=ua,
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True,
            )
            page = context.pages[0] if context.pages else context.new_page()
            # ... (stealth + navegación tal cual) ...
            html = page.content()
            context.close()
```

**Consideraciones:**
- Los directorios `tor_data/pw_profile_dorks`, `pw_profile_fb`, `pw_profile_ig` se crean automáticamente y NO deben borrarse entre ejecuciones (ahí viven las cookies).
- `os` ya se importa en los 3 scrapers (verificar con `grep "^import os"`).
- En `instagram_dorks.py`, el `context.close()` reemplaza al `browser.close()` del bloque except.

---

## MEJORA 2 — Parámetro `&gbv=1` (Google Basic View)

**Fuente:** iwanghc/mcp_web_search, web-agent-master/google-search. **Impacto:** MEDIO-ALTO (versión estática sin JS de fingerprinting).

**Qué cambiar:** añadir `&gbv=1` a `search_url` en los 3 scrapers.

### Los 3 archivos — en la construcción de `search_url`

```python
                search_url = (
                    f"https://www.google.com/search?q={urllib.parse.quote_plus(dork)}"
                    f"&hl=en&num=10&filter=0&pws=0&gbv=1"
                )
```

**Consideración:** con `gbv=1` el selector `div.g` puede variar (estructura HTML antigua). Si tras aplicar no se parsean resultados, cambiar `wait_for_selector("div.g")` a `wait_for_selector("div.g", timeout=8000)` y si falla, esperar `page.wait_for_load_state("domcontentloaded")` y extraer igualmente. Opcional: probar con y sin `gbv=1` alternando.

---

## MEJORA 3 — Cookie `GOOGLE_ABUSE_EXEMPTION`

**Fuente:** opsdisk/yagooglesearch PR #21. **Impacto:** ALTO si se cosecha tras resolver 1 CAPTCHA manual.

**Qué añadir:** un módulo compartido que guarde/cargue cookies y las inyecte con `context.add_cookies()`.

### 3a. Crear `core/google_cookies.py`

```python
"""Persistencia de cookies de Google (GOOGLE_ABUSE_EXEMPTION, CONSENT, SOCS).

Tras resolver un CAPTCHA manualmente una vez en un navegador visible,
guardar las cookies con guardar_cookies() y reutilizarlas en las búsquedas.
"""
import json
import os
from typing import Dict, List

_RUTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tor_data", "google_cookies.json")


def guardar_cookies(cookies: List[Dict]) -> None:
    """Guarda cookies (formato Playwright) a disco."""
    try:
        os.makedirs(os.path.dirname(_RUTA), exist_ok=True)
        with open(_RUTA, "w") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def cargar_cookies() -> List[Dict]:
    """Carga cookies guardadas. Retorna [] si no existen."""
    try:
        if os.path.exists(_RUTA):
            with open(_RUTA) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def cookie_google(filtro: str) -> str:
    """Devuelve el valor de una cookie (ej: 'GOOGLE_ABUSE_EXEMPTION')."""
    for c in cargar_cookies():
        if c.get("name") == filtro:
            return c.get("value", "")
    return ""
```

### 3b. Inyectar en los 3 scrapers — después de crear el `context` y antes de navegar

**dorks_resultados.py y facebook_dorks.py (async):**
```python
                try:
                    from core.google_cookies import cargar_cookies
                    _cks = cargar_cookies()
                    if _cks:
                        await context.add_cookies(_cks)
                except Exception:
                    pass
```

**instagram_dorks.py (sync):**
```python
                try:
                    from core.google_cookies import cargar_cookies
                    _cks = cargar_cookies()
                    if _cks:
                        context.add_cookies(_cks)
                except Exception:
                    pass
```

### 3c. Cosechar la cookie una vez (script manual `tools/cosechar_cookie.py`)

```python
"""Abre Chrome visible con el proxy de Tor y espera a que resuelvas un CAPTCHA.

Al detectar la cookie GOOGLE_ABUSE_EXEMPTION la guarda en tor_data/google_cookies.json.
Uso: source venv/bin/activate && python3 tools/cosechar_cookie.py
"""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.tor_pool import obtener_sesion_tor, verificar_tor
from core.google_cookies import guardar_cookies
from playwright.async_api import async_playwright


async def _run():
    socks_port = 9050
    if verificar_tor():
        s = obtener_sesion_tor(0)
        if s and s.proxies:
            for v in s.proxies.values():
                if "socks5" in v:
                    import re
                    m = re.search(r":(\d+)$", v)
                    if m:
                        socks_port = int(m.group(1))
                        break
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            os.path.join("tor_data", "pw_profile_cosecha"),
            headless=False,
            proxy={"server": f"socks5://127.0.0.1:{socks_port}"},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.google.com/search?q=test", timeout=60000)
        print("Resuelve el CAPTCHA en la ventana... esperando GOOGLE_ABUSE_EXEMPTION")
        for _ in range(60):
            cks = await context.cookies()
            if any(c["name"] == "GOOGLE_ABUSE_EXEMPTION" for c in cks):
                guardar_cookies(cks)
                print("Cookies guardadas. Ya puedes cerrar.")
                return
            await asyncio.sleep(2)
        # Guardar igualmente lo que haya (CONSENT, SOCS)
        guardar_cookies(await context.cookies())
        print("Sin GOOGLE_ABUSE_EXEMPTION, pero se guardó lo existente.")
        await context.close()


asyncio.run(_run())
```

---

## MEJORA 4 — Parche JS `navigator.webdriver`

**Fuente:** VincentKaufmann/noapi-google-search-mcp. **Impacto:** MEDIO.

**Qué cambiar:** añadir `page.add_init_script()` en los 3 scrapers (después de crear `page`, antes de navegar).

### Los 3 scrapers

```python
                # Ocultar automatización antes de cualquier navegación
                try:
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = window.chrome || {runtime: {}};
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    """)
                except Exception:
                    pass
```

**instagram_dorks.py (sync):** `page.add_init_script(...)` (sin `await`).

---

## MEJORA 5 — Usar `undetected-playwright` (ya instalado)

**Fuente:** ultrafunkamsterdam. **Impacto:** MEDIO (parchea WebDriver/Chrome a nivel de launcher).

**Qué cambiar:** en los 3 scrapers, sustituir el bloque de `async_playwright()` / `sync_playwright()` + `chromium.launch()` por el launcher de `undetected_playwright` cuando esté disponible, manteniendo Tor.

**Patrón (dorks_resultados.py, async):**
```python
        try:
            from undetected_playwright import stealth as _u_stealth, async_playwright as _u_pw
            _USE_U = True
        except Exception:
            _USE_U = False

        async def _run_real():
            pw_mod = _u_pw() if _USE_U else async_playwright()
            async with pw_mod as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage",
                          "--disable-blink-features=AutomationControlled"],
                )
                # ... resto igual (new_context, proxy, stealth, navegación) ...
```

**Instagram (sync):** usar `undetected_playwright.sync_playwright`.

**Nota:** `undetected-playwright` ya está en el venv (v0.3.0). Verificar compatibilidad con la versión de chromium instalada antes de hacerlo default; si falla el launch, caer al `async_playwright` normal (try/except).

---

## MEJORA 6 — Parseo de `/sorry/index?continue=`

**Fuente:** Ekultek/Zeus-Scanner. **Impacto:** BAJO (solo recupera la URL de búsqueda cuando Google bloquea).

**Qué añadir:** en `_buscar_google_con_reintentos` (los 3 scrapers), antes de descartar el HTML, detectar `/sorry/index` y extraer `continue=`, que contiene la URL de búsqueda con `q=`. Retornar ese URL como "resultado" recuperable (para al menos saber que el IP está bloqueado y rotar).

```python
                # Detección de página de bloqueo /sorry (Google)
                m_sorry = re.search(r"https?://[^\"']*?/sorry/index\?[^\"']*continue=([^&\"']+)",
                                    html)
                if m_sorry:
                    _bloqueo_detectado_en_dork = True
                    print("    ⚠️ Google bloqueó con /sorry/index — rotando Tor")
                    # (el flujo ya rotará en el siguiente intento)
```

**Además:** en `_limpiar_url_google`, si aparece `/sorry/index`, devolver `None` y filtrarlo de resultados.

---

## MEJORA 7 — Resolución manual de CAPTCHA (headful)

**Fuente:** San-Tus/Dorkwright, google-surf-mcp. **Impacto:** MEDIO (solo útil si un humano está presente).

**Qué añadir:** en `_buscar_google_con_reintentos` (3 scrapers), si se detecta captcha en el HTML **y** `CAPTCHA_MANUAL = True` (constante), reabrir en modo visible y esperar input:

```python
# Constante al inicio de cada scraper:
CAPTCHA_MANUAL = os.environ.get("CAPTCHA_MANUAL", "0") == "1"
```

```python
        if CAPTCHA_MANUAL and ("captcha" in low or "recaptcha" in low):
            print("   ⛔ CAPTCHA detectado. Resuélvelo en la ventana y presiona ENTER...")
            input()
            # Reintentar la búsqueda ahora que el humano resolvió
            html = _google_search_playwright(dork, indice_tor)
```

**Nota:** solo tiene sentido con `headless=False`. Se puede combinar con la MEJORA 3: tras resolver, `guardar_cookies()` para futuras ejecuciones.

---

## MEJORA 8 — Backoff exponencial tras 429

**Fuente:** opsdisk/yagooglesearch. **Impacto:** MEDIO (se integra con la regla de esperar 30-60 min).

**Qué cambiar:** en `_buscar_google_con_reintentos` (3 scrapers), tras N bloqueos consecutivos globales, añadir espera exponencial:

```python
# Al inicio de cada scraper:
_BLOQUEOS_GLOBALES = 0
```

```python
        # Tras un bloqueo, incrementar contador global
        _BLOQUEOS_GLOBALES += 1
        if _BLOQUEOS_GLOBALES >= 3:
            espera = min(45 * 60, 1.5 ** _BLOQUEOS_GLOBALES * 300)  # 5min, 7.5, 11...
            print(f"    ⏳ Backoff tras bloqueos: {espera:.0f}s")
            time.sleep(espera)
            _BLOQUEOS_GLOBALES = 0
```

**Consideración:** respetar el límite de la tarea (esperar 30-60 min cuando todo falla). El backoff corto (5-11 min) es interno entre dorks; el esperar 30-60 min aplica cuando TODA la ejecución devuelve 0.

---

## Orden de aplicación recomendado (1 ejecución por paso)

| Paso | Mejora | Verificación |
|------|--------|--------------|
| 1 | MEJORA 2 (`&gbv=1`) — 1 línea por archivo | `scrape_dorks_resultados(limite=1)` sin errores |
| 2 | MEJORA 1 (persistent context) | revisar que se crean `tor_data/pw_profile_*` |
| 3 | MEJORA 4 (parche JS) | sin errores en consola |
| 4 | MEJORA 6 (parseo /sorry) | ver "⚠️ bloqueó con /sorry" en log |
| 5 | MEJORA 3 + 3c (cookie exemption) | ejecutar `tools/cosechar_cookie.py` 1 vez |
| 6 | MEJORA 8 (backoff) | log muestra esperas entre dorks |
| 7 | MEJORA 5 (undetected-playwright) | probar y si falla, revertir a la MEJORA 1 |
| 8 | MEJORA 7 (CAPTCHA manual) | solo si hay humano supervisando |

**Regla de oro:** aplicar una mejora a la vez, ejecutar el scraper, y solo pasar a la siguiente si no hay errores de código nuevos. Los 0 resultados siguen siendo esperados mientras Google bloquee el IP de Tor; el objetivo de estas mejoras es reducir la tasa de bloqueo, no garantizarla.

## Script de prueba estándar

```bash
cd "$(dirname "$0")"
source venv/bin/activate
python3 -u -c "
from scrapers.dorks_resultados import scrape_dorks_resultados
print('Eventos:', len(scrape_dorks_resultados(limite=1)))
"
```

## Backups antes de cada cambio

```bash
cp scrapers/dorks_resultados.py backup_$(date +%Y%m%d_%H%M%S)_dr.py
cp scrapers/facebook_dorks.py backup_$(date +%Y%m%d_%H%M%S)_fb.py
cp scrapers/instagram_dorks.py backup_$(date +%Y%m%d_%H%M%S)_ig.py
```
