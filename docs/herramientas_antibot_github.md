# Herramientas anti-bloqueo de Google / Tor — Investigación GitHub

> Fecha: 2026-08-16 · Propósito: referencia para futuras búsquedas sobre cómo resolver el bloqueo de Google (CAPTCHA/429/consent) al hacer scraping de dorks a través de Tor con Playwright.
> Criterios: **gratis**, **open source**, **compatible con nuestro stack** (Python 3.9.6, venv, Playwright 1.60, requests, Tor pool en 9050/9052/9054), y **instalable en local** dentro de la carpeta del proyecto.

---

## Contexto del problema

Google bloquea los IPs de salida de Tor (exit nodes) casi inmediatamente. La comunidad confirma que **"usar Tor con Google hace que te bloqueen rápido"** (opsdisk, autor de yagooglesearch). Estrategias útiles encontradas en la comunidad:

| Estrategia | Fuente | Descripción |
|-----------|--------|-------------|
| Cookie `GOOGLE_ABUSE_EXEMPTION` | opsdisk/yagooglesearch PR #21, issue #43 | Tras resolver un CAPTCHA manualmente una vez, Google coloca esta cookie que exime futuras búsquedas del mismo IP. Se cosecha y reutiliza. |
| Parámetro `&gbv=1` (Basic View) | iwanghc/mcp_web_search, web-agent-master/google-search | Fuerza la versión HTML estática de Google, que no ejecuta JS de fingerprinting anti-bot. |
| Contexto persistente del navegador | mcp_web_search, HarimxChoi/google-surf-mcp | Guardar cookies/localStorage en un perfil persistente → Google ve un "browser establecido" y reduce la frecuencia de CAPTCHAs. |
| Resolución manual de CAPTCHA | San-Tus/Dorkwright, google-surf-mcp, giveen/mcp_web_search | Detectar CAPTCHA, abrir navegador visible, pausar y esperar a que un humano resuelva, luego continuar automáticamente. |
| Solver neuronal de reCAPTCHA | VincentKaufmann/noapi-google-search-mcp | MobileNetV2 + OpenCV resuelve automáticamente reCAPTCHA de imágenes ("selecciona todos los semáforos"). |
| Extraer URL del redirect `/sorry` | Ekultek/Zeus-Scanner | Cuando Google bloquea con `/sorry/index?continue=<URL>`, el redirect contiene la búsqueda original. |
| Backoff exponencial tras 429 | opsdisk/yagooglesearch | Tras HTTP 429, esperar `45 min × 1.5^n` antes de reintentar. |
| Round-robin de proxies | opsdisk/pagodo | Múltiples proxies `socks5h://` en rotación. Ya lo tenemos con `tor_pool`. |
| `undetected-playwright` | ultrafunkamsterdam (sponsor) | **Ya instalado en nuestro venv (v0.3.0) pero NO se usa.** Parchea WebDriver/Chrome para evadir detección. |

---

## Herramientas concretas encontradas

### 1. mcp_web_search (iwanghc) — ⭐ RECOMENDADA, la más relevante
- **URL:** https://github.com/iwanghc/mcp_web_search · (fork del original también: web-agent-master/google-search)
- **Licencia:** MIT · **Lenguaje:** Python 3.8+ (compatible con 3.9.6) · Playwright
- **Qué hace:** Buscador Google que **bypasea anti-bot** con éxito del 95%+ usando estado persistente del navegador.
- **Técnicas que aplica (todas portables a nuestros scrapers):**
  - **Persistent context** (`launch_persistent_context`) guardando cookies/localStorage en `./user_data` y `browser-state.json` → Google ve un perfil establecido, reduce CAPTCHAs.
  - **`&gbv=1` (Google Basic View)**: fuerza HTML estático sin JS de fingerprinting → esquiva gran parte de la detección basada en JS.
  - Cambio automático headless ↔ headed cuando detecta verificación.
  - Retry automático: si hay CAPTCHA en modo moderno, reintenta en Basic View.
  - Randomización de fingerprint/dispositivo/locale.
- **Instalación local:**
  ```bash
  cd /Users/angelgarcia/dharmadhatu_agent_qwen/tools
  git clone https://github.com/iwanghc/mcp_web_search.git
  cd mcp_web_search
  source ../../venv/bin/activate
  pip install -r requirements.txt
  # Nota: la instalación de Playwright browsers ya existe en el venv
  ```
- **Uso como CLI:** `python cli.py --manual-captcha "site:facebook.com events psytrance"`
- **Portabilidad a nuestros scrapers:** copiar el manejo de `persistent_context` + `&gbv=1` + guardado de cookies. Esto es lo que más falta en `scrapers/dorks_resultados.py`.

### 2. noapi-google-search-mcp (VincentKaufmann) — solver de CAPTCHA neuronal
- **URL:** https://github.com/VincentKaufmann/noapi-google-search-mcp
- **Licencia:** MIT · **Lenguaje:** Python, pip install · Playwright + OpenCV
- **Qué hace:** MCP server de búsqueda Google con **solver neuronal de reCAPTCHA** integrado.
- **Técnicas:**
  - **Neural net CAPTCHA solver**: MobileNetV2 (ImageNet, ~13MB ONNX, auto-descarga) + OpenCV divide la cuadrícula y clasifica cada celda. Cubre 22 categorías (semáforos, buses, bicicletas, coches...).
  - Inyección JS antes de cada carga: parchea `navigator.webdriver`, plugins falsos, `chrome.runtime`, quita fingerprints de Playwright.
  - **Persistencia de cookies entre sesiones** → Google ve un browser que regresa.
  - Delays humanos aleatorios entre interacciones.
- **Instalación local:**
  ```bash
  pip install noapi-google-search-mcp
  playwright install chromium   # ya disponible en el venv
  ```
- **Portabilidad:** el código del solver (MobileNetV2+ONNX+OpenCV) se puede extraer para resolver CAPTCHAs de imágenes en nuestros dorks. El parche JS de `navigator.webdriver` es aplicable directamente en `_google_search_playwright`.

### 3. google-surf-mcp (HarimxChoi) — CAPTCHA recovery con Chrome real
- **URL:** https://github.com/HarimxChoi/google-surf-mcp
- **Licencia:** MIT · **Lenguaje:** Node.js 18+ (requiere Google Chrome/Chromium instalado)
- **Qué hace:** MCP de búsqueda Google sin API key. **Cuando hay CAPTCHA, abre una ventana de Chrome visible para que un humano resuelva una vez, la llamada se reintenta y el perfil conserva reputación.**
- **Concepto clave:** "Cada resolución preserva la reputación del perfil con Google." Requiere un perfil "warm" (bootstrap: hacer 1 búsqueda manual primero).
- **Compatibilidad:** Es Node.js, no Python → no instalar en nuestro venv. **El concepto sí es portable**: usar perfil persistente + resolver CAPTCHA una vez y reutilizar cookies.

### 4. San-Tus/Dorkwright — dorking con CAPTCHA-aware
- **URL:** https://github.com/San-Tus/Dorkwright
- **Licencia:** MIT · **Lenguaje:** Python + Playwright
- **Qué hace:** Automatiza Google Dorks, extrae URLs de archivos, con **manejo de CAPTCHA**: cuando detecta CAPTCHA, espera a que el usuario lo resuelva en la ventana del navegador y presiona ENTER.
- **Instalación:** `git clone` + `pip install -r requirements.txt`
- **Portabilidad:** patrón de "CAPTCHA DETECTED → esperar resolución manual → continuar" fácil de añadir a `_buscar_google_con_reintentos`.

### 5. serp-scraper (neuronaline) — Camoufox stealth browser
- **URL:** https://github.com/neuronaline/serp-scraper
- **Licencia:** (open source) · **Lenguaje:** Python **3.10+** (⚠️ NO compatible con nuestro 3.9.6)
- **Qué hace:** Scraper de SERP Google/Bing con **Camoufox** (Firefox stealth parcheado, más difícil de detectar que Chromium), rotación de proxies, caché, CAPTCHA handling con backoff exponencial.
- **Nota:** Requiere `python -m camoufox fetch && python -m camoufox set official` y Python 3.10+. **No instalar tal cual**; el concepto de Camoufox es relevante como alternativa a Chromium.

### 6. FlareSolverr — bypass Cloudflare (no Google)
- **URL:** https://github.com/FlareSolverr/FlareSolverr
- **Licencia:** MIT · **Lenguaje:** Python 3.11+ / Docker · Selenium + undetected-chromedriver
- **Qué hace:** Proxy server que resuelve challenges de **Cloudflare y DDoS-GUARD** (no Google) usando un Chrome real. Devuelve HTML + cookies `cf_clearance` reutilizables.
- **Instalación:** Docker (`docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest`) o binario para Windows/Linux.
- **Nota:** Los solvers de CAPTCHA integrados **no funcionan** según el propio README (issues abiertos). Útil solo para Cloudflare, no para el bloqueo de Google por Tor.

### 7. ohmycaptcha (shenhao-stu) — solver self-hosted tipo YesCaptcha
- **URL:** https://github.com/shenhao-stu/ohmycaptcha
- **Licencia:** MIT · **Lenguaje:** Python, FastAPI + Playwright · 822 stars
- **Qué hace:** Solver de CAPTCHA self-hosted con API estilo YesCaptcha (`createTask`/`getTaskResult`). 19 tipos: reCAPTCHA v2/v3, hCaptcha, Turnstile, clasificación de imágenes.
- **Requiere:** modelo multimodal local (Qwen3.5-2B vía SGLang) o cloud. Pesado para una máquina Mac sin GPU.
- **Nota:** Más orientado a servidores. El modelo local de 2B parámetros es demasiado pesado para nuestro entorno; se menciona como referencia.

### 8. waguriagentic/captcha-solver — sidecar HTTP local (CloakBrowser)
- **URL:** https://github.com/waguriagentic/captcha-solver
- **Licencia:** (open source) · **Lenguaje:** Python + `cloakbrowser` (anti-detect Chromium)
- **Qué hace:** Sidecar HTTP local que resuelve 11 tipos de challenges (Turnstile, reCAPTCHA v2/v3, hCaptcha, Cloudflare, AWS WAF, BotGuard) ejecutándolos en un navegador real.
- **Nota:** Solver "harvest-only" (no crea cuentas). Requiere dependencia `cloakbrowser`. Potencialmente útil, pero hay que validar peso e instalación en Mac.

### 9. opsdisk/yagooglesearch — la referencia académica (no instalable, ya estudiado)
- **URL:** https://github.com/opsdisk/yagooglesearch
- **Ya analizado** en sesiones previas. Claves reutilizables:
  - Parámetro `google_exemption` = cookie `GOOGLE_ABUSE_EXEMPTION`.
  - `http_429_cool_off_time_in_minutes=45`, `http_429_cool_off_factor=1.5` (backoff exponencial).
  - Delays 7-17s entre páginas, 30-60s entre búsquedas.
  - Cookie `CONSENT=YES+shp.gws-20211108-0-RC1.fr+F+{numero}` para IPs de la UE (evita redirect a consent.google.com).
  - **Aviso explícito del autor: usar Tor → bloqueo rápido.** La solución real es: IP de EE.UU. (VPS/VPN), cookies de exención, y esperas largas.

---

## Decisiones y hallazgos para NUESTRO código

### Lo que YA tenemos en el proyecto
- [x] Pool de Tor (3 circuitos: 9050/9052/9054) en `core/tor_pool.py`
- [x] Retry con rotación de Tor (`_buscar_google_con_reintentos`)
- [x] User-Agent rotativo (5 UAs desktop)
- [x] Cookies `CONSENT` + `SOCS`
- [x] Parámetros `&filter=0&pws=0&num=10`
- [x] Delays aleatorios 7-18s
- [x] Detección de "unusual traffic"/captcha/consent en el HTML
- [x] `playwright-stealth` (Stealth) aplicado en las páginas
- [x] `undetected-playwright` **instalado en el venv pero sin usar**

### Lo que FALTA y es accionable (sin instalar nada nuevo)
1. **✅ IMPLEMENTADO** — **Contexto persistente** (`launch_persistent_context` con `user_data_dir` en `tor_data/pw_profile_*`) → Google ve un perfil establecido. Ver `core/google_stealth.py`.
2. **✅ IMPLEMENTADO** — **`&gbv=1`** en la URL de búsqueda → versión estática sin fingerprinting JS.
3. **⏳ PENDIENTE** — **Cookie `GOOGLE_ABUSE_EXEMPTION`**: guardar en un JSON las cookies tras resolver un CAPTCHA manualmente una vez, y reinyectarlas en `context.add_cookies()`.
4. **✅ IMPLEMENTADO** — **Parche JS de `navigator.webdriver`** antes de navegar (patrón de noapi-google-search-mcp).
5. **✅ IMPLEMENTADO** — **Usar `undetected-playwright`** + `playwright-stealth` (dual stealth).
6. **⏳ PENDIENTE** — **Detección y parseo de `/sorry/index?continue=...`** para recuperar la URL original (Zeus-Scanner).
7. **⏳ PENDIENTE** — **Modo de resolución manual de CAPTCHA**: si el HTML contiene captcha → `headless=False`, esperar input, continuar.

### Lo que requeriría instalar (opcional, a decisión)
- `noapi-google-search-mcp` → solver neuronal de reCAPTCHA (MobileNetV2 ONNX, ~13MB).
- `mcp_web_search` (clonar en `tools/`) → tomarlo como referencia de persistent_context + gbv.

---

## Cómo verificar rápidamente una técnica

```bash
cd /Users/angelgarcia/dharmadhatu_agent_qwen
source venv/bin/activate
python3 -u -c "
from scrapers.dorks_resultados import scrape_dorks_resultados
evs = scrape_dorks_resultados(limite=1)
print('Eventos:', len(evs))
"
```

Esperar resultado esperado: **0 eventos pero SIN errores de código** es el comportamiento actual. Si tras aplicar persistent_context/gbv se obtienen resultados reales, es éxito.

---

## Checklist de soluciones implementadas (2026-08-16)

1. [x] `launch_persistent_context` con `user_data_dir` (perfil persistente en `tor_data/pw_profile_*`)
2. [x] `&gbv=1` en la URL de búsqueda (Google Basic View)
3. [ ] Cookie `GOOGLE_ABUSE_EXEMPTION` (cosechar 1 vez manualmente)
4. [x] Parche JS `navigator.webdriver` + `--disable-blink-features=AutomationControlled`
5. [x] `undetected-playwright` + `playwright-stealth` (dual stealth)
6. [ ] Parseo de `/sorry/index?continue=`
7. [ ] Modo manual CAPTCHA (headful + esperar input)
8. [ ] Backoff exponencial 45min×1.5^n tras 429 (convivir con regla de esperar 30-60 min)

---

## Nota sobre IPs de salida
El autor de yagooglesearch es claro: **Google bloquea Tor rápido**. Incluso con todas las técnicas, el mejor resultado puede ser limitado si Google categoriza los IPs de Tor como de alto riesgo. La alternativa más efectiva documentada es IP de EE.UU. (VPS/VPN) + cookies de exención, pero **viola la regla de solo-Tor del proyecto** → documentar aquí solo como referencia, no aplicable.
