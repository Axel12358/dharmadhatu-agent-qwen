# Reglas de Tor para scraping

## Principio innegociable

- Todo el scraping de Facebook e Instagram se ejecuta a través de Tor.
- Se usa el proxy SOCKS5 en `127.0.0.1:9050` (socks5h:// para requests, socks5:// para Playwright).
- La IP real nunca debe usarse.

## Configuración obligatoria

- `requests`:
  ```python
  proxies = {
      "http": "socks5h://127.0.0.1:9050",
      "https": "socks5h://127.0.0.1:9050"
  }
  ```

- `Playwright`:
  ```python
  proxy = {"server": "socks5://127.0.0.1:9050"}
  context = await browser.new_context(proxy=proxy, user_agent="...")
  ```

- `aiohttp`:
  ```python
  proxy="socks5://127.0.0.1:9050"
  ```

- `httpx`:
  ```python
  proxies={"http://": "socks5://127.0.0.1:9050", "https://": "socks5://127.0.0.1:9050"}
  ```

## Tabla de estado de módulos

| Módulo | Usa Tor | Razón |
|--------|---------|-------|
| `scrapers/facebook_dorks.py` | ✅ SÍ | Google SOLO vía Playwright+Tor+stealth. Si bloquea → rotar Tor + reintentar (máx 3). Usa `tor_pool` para multi-circuito. Otros motores desactivados. |
| `scrapers/dorks_resultados.py` | ✅ SÍ | Google SOLO vía Playwright+Tor+stealth. Si bloquea → rotar Tor + reintentar (máx 3). Usa `tor_pool` para multi-circuito. Otros motores desactivados. |
| `scrapers/instagram_dorks.py` | ✅ SÍ | Google SOLO vía Playwright+Tor+stealth. Si bloquea → rotar Tor + reintentar (máx 3). Usa `tor_pool` para multi-circuito. Otros motores desactivados. |
| `core/tor_manager.py` | ✅ SÍ | Delega a `tor_pool` para gestión profesional de circuitos. Provee `obtener_proxy_tor()`, `obtener_sesion_tor()`, `renovar_identidad_tor()`, `_reiniciar_proceso_tor()`. `TOR_SOCKS_HOST=127.0.0.1`, `TOR_SOCKS_PORT=9050`. |
| `core/tor_pool.py` | ✅ SÍ | Pool profesional de Tor: `IDENTIDADES_TOR = [(9050, 9051), (9052, 9053), (9054, 9055)]`, `iniciar_pool`, `obtener_sesion_tor`, `rotar_tor`, `marcar_circuito_bloqueado`, `obtener_siguiente_identidad`, `verificar_tor`. Auto-inicializa pool. |
| `core/google_stealth.py` | ✅ SÍ | Módulo compartido de anti-bloqueo Google: `_run_google_search_async/sync` (Playwright persistente + stealth + gbv=1 + Tor), `apply_stealth_async/sync` (dual: undetected-playwright + playwright-stealth), `get_tor_port`, `get_ua`, `load/save_storage_state`, `CONSENT_COOKIES`, `_INIT_SCRIPT` (parche navigator.webdriver). Perfiles persistentes en `tor_data/pw_profile_*`. |
| `core/tor_utils.py` | ✅ SÍ | Utilities: `verificar_tor`, `rotar_tor` (subprocess fallback, sin Stem), `obtener_sesion_tor` (Tor-only, lanza excepción si no disponible). |
| `core/recursos.py` | ✅ SÍ (actualizado) | `obtener_proxies_dict()` ahora prioriza Tor (`socks5h://127.0.0.1:9050`) si está disponible vía `tor_pool`. Nueva función `obtener_proxies_tor()` para proxy Tor explícito. |
| `core/agente_coordinador.py` | ✅ SÍ | Retry logic con `MAX_RETRIES_TOR = 3`, rotación Tor entre intentos, `SEMAFORO_TOR` (max 1 request concurrente). |
| `probar_dorks.py` | ✅ SÍ | Runner: 3 scrapers con max 3 retries + Tor rotation + status display + resumen table. |
| `scrapers/goabase.py` | ⚠️ NO | `requests.get("https://www.goabase.net/api/party/jsonld/")` sin proxy. API pública, pero petición directa. Añadir Tor sería cambiar la URL base o usar sesión con proxy, riesgo de romper el endpoint JSON‑LD. |
| `scrapers/songkick.py` | ⚠️ NO | `requests.get(url, headers=headers, timeout=30)` directo. Motor de búsqueda de eventos; bloqueos frecuentes en IP de datacenter. Añadir Tor requiere reestructurar la lógica de búsqueda. |
| `scrapers/resident_advisor.py` | ⚠️ NO | Peticiones a `ra.co/graphql` vía `requests`. API GraphQL pública; la web ra.co bloquea con DataDome. Añadir Tor al GraphQL es viable pero no probado en este proyecto. |
| `scrapers/facebook_mcp.py` | ✅ SÍ (parcial) | Usa `anti_block.get_proxy()` que devuelve o bien proxy de `proxies.txt` o bien `socks5://127.0.0.1:9050` (Tor). líneas 1104‑1105 y 1709‑1721 ya tienen lógica de Tor. El contexto Playwright con Tor también está implementado (líneas 390‑400 de anti_block.py). **Tor está disponible pero no es el camino único**; aún usa proxies de fichero con rotación. |
| `scrapers/facebook_events_from_groups.py` | ⚠️ NO | `requests.GET` a `mbasic.facebook.com` con timeout 8s, sin proxy Tor. Fallback a Google → Startpage (sin Tor). Añadir Tor requiere modificar la petición principal y los fallbacks. |
| `scrapers/instagram_scraper.py` | ⚠️ NO | `requests` sobre `instagram.com/explore/tags/` y Playwright opcional. Filosofía "suma, nunca resta"; está diseñado para no modificar otros scrapers. Añadir Tor requeriría reescribir los tres métodos (A/B/C) y no está incluido por riesgo de romper la filosofía aditiva. |
| `scrapers/anti_block.py` | ✅ SÍ | Motor de proxies y detección de bloqueos. Tiene `get_socks_proxy()` que retorna `socks5://127.0.0.1:9050` y contexto Playwright con Tor (línea 390‑400). Es la pieza central para habilitar Tor en los demás módulos. |
| `core/recursos.py` | ⚠️ NO | `obtener_proxy()` lee de `proxies.txt` (HTTP data‑center). `obtener_proxies_dict()` retorna dict `http/https`. No integra Tor; añadilo en el caller si lo necesitas. |

## Arquitectura del pool de Tor

- `core/tor_pool.py` gestiona un pool de múltiples circuitos Tor simultáneos:
  - `IDENTIDADES_TOR = [(9050, 9051), (9052, 9053), (9054, 9055)]` — 3 identidades.
  - `iniciar_pool()` → arranca/inicia los circuitos (auto-inicializa en `obtener_sesion_tor`).
  - `obtener_sesion_tor(indice)` → devuelve `requests.Session` con `socks5h://127.0.0.1:<port>`.
  - `rotar_tor(indice)` → rota la identidad del circuito indicado.
  - `marcar_circuito_bloqueado(indice)` → marca circuito como bloqueado (cooldown).
  - `obtener_siguiente_identidad()` → índice del circuito menos usado/no bloqueado.
  - `verificar_tor()` → True si al menos 1 circuito está activo.
  - Estado persistente en `tor_data/tor_pool_estado.json`.

## Motor de búsqueda: Google SOLO

- El flujo automático de scraping usa **única y exclusivamente Google** a través de **Playwright + Tor + stealth**.
- Los motores secundarios (DDG, Mojeek, Bing, Startpage) están **desactivados** en el flujo automático.
- Si Google bloquea (CAPTCHA, consentimiento, "unusual traffic"):
  1. Se guarda el `storage_state` del contexto (cookies/localStorage) para preservar reputación.
  2. Se cierra el navegador.
  3. Se rota la identidad Tor (circuito del pool).
  4. Se espera 20-40s (pausa humana).
  5. Se reintenta el mismo dork con un nuevo contexto (máx 3 intentos).
  6. Si sigue bloqueado tras 3 intentos, se pasa al siguiente dork.
- **Nunca** se usa la IP real: todo pasa por `socks5://127.0.0.1:<puerto_pool>` via Playwright proxy.

### Técnicas anti-bloqueo implementadas

1. **Contexto persistente** (`launch_persistent_context`): Google ve un perfil establecido (cookies/localStorage persisten entre sesiones en `tor_data/pw_profile_*`). Reduce frecuencia de CAPTCHAs.
2. **`&gbv=1`** en todas las URLs de búsqueda: fuerza Google Basic View (HTML estático sin JS de fingerprinting).
3. **`storage_state`**: se guarda al final de cada sesión exitosa y se reutiliza en la siguiente (cookies, localStorage).
4. **Parche JS `navigator.webdriver`**: `Object.defineProperty(navigator, 'webdriver', {get: () => undefined})` antes de cada navegación.
5. **Dual stealth**: `undetected-playwright` (parchea Chrome a nivel de launcher) + `playwright-stealth` (fingerprint de página). Se usa el que esté disponible.
6. **UA rotativo**: pool de 5 User-Agents de escritorio actualizados (Chrome 131, Firefox 133, Edge 128, Safari 18.1).
7. **Cookies CONSENT+SOCS**: preconfiguradas para evitar redirects a `consent.google.com`.
8. **Pausas 20-40s**: entre reintentos y entre dorks (simula tráfico humano real).
9. **Detección ampliada**: "unusual traffic", "captcha", "recaptcha", "sorry", `/sorry/index?continue=`.

- **Extracción**: URL real (limpia `/url?q=`), título (`h3`), snippet (`span.st` / `div[data-sncf]`).

- `core/tor_manager.py` expone las funciones clave (delega a `tor_pool` cuando está disponible):
  - `obtener_proxy_tor()` → `{"http": "socks5h://127.0.0.1:9050", "https": "socks5h://127.0.0.1:9050"}`
  - `obtener_sesion_tor()` → `requests.Session` ya con proxy Tor y headers rotados.
  - `renovar_identidad_tor()` → rota IP mediante `tor_pool.rotar_tor()` o ControlPort.
  - `tor_disponible()` → chequeo de puerto 9050 (cacheado 60s).
- `core/recursos.py` ahora prioriza Tor: `obtener_proxies_dict()` retorna `socks5h://127.0.0.1:9050` si `tor_pool.verificar_tor()` es True; si no, cae a `proxies.txt`.
- **Semáforo global**: máximo 1 petición Tor simultánea (configurable en `TOR_SEMAPHORE` de los scrapers dorks y `SEMAFORO_TOR` en `agente_coordinador.py`).

## Regla de rotación de identidad

- Rotar Tor cada `TOR_ROTATE_EVERY` consultas (valor: 1, antes de cada dork) con pausa de 10-20s.
- En los 3 scrapers de dorks: `TOR_ROTATE_EVERY = 1` (rotación máxima), `TOR_RESTART_PAUSE = 10`.
- **Pausas entre reintentos**: 20-40s (simula tráfico humano, recomendación de yagooglesearch/Dorkwright).
- Siempre que un motor devuelva `403`/`429`/`503` con bloqueo detectado, se rota la identidad antes de la siguiente petición.

## Resumen de aplicación

- **Modificados y forzados a Tor:** `scrapers/facebook_dorks.py`, `scrapers/dorks_resultados.py`, `scrapers/instagram_dorks.py`.
- **Integrados con tor_pool:** `core/tor_manager.py`, `core/recursos.py`, `core/agente_coordinador.py`.
- **Ya tenían Tor:** `anti_block.py` (motor central), `facebook_mcp.py` (usa `anti_block.get_proxy()` con opción Tor).
- **No modificados (por riesgo/rochen):** `goabase.py`, `songkick.py`, `resident_advisor.py`, `facebook_events_from_groups.py`, `instagram_scraper.py`. Documentados en la tabla anterior.
## Resumen de aplicación

- **Modificados y forzados a Tor + Google-only:** `scrapers/facebook_dorks.py`, `scrapers/dorks_resultados.py`, `scrapers/instagram_dorks.py`.
- **Integrados con tor_pool:** `core/tor_manager.py`, `core/recursos.py`, `core/agente_coordinador.py`.
- **Ya tenían Tor:** `anti_block.py` (motor central), `facebook_mcp.py` (usa `anti_block.get_proxy()` con opción Tor).
- **No modificados (por riesgo/rochen):** `goabase.py`, `songkick.py`, `resident_advisor.py`, `facebook_events_from_groups.py`, `instagram_scraper.py`. Documentados en la tabla anterior.
- **Objetivo cumplido:** todos los scrapers de dorks usan Google SOLO a través de Playwright+Tor+stealth con contexto persistente + gbv=1; `core/google_stealth.py` centraliza la lógica anti-bloqueo; `tor_manager` delega al pool; `recursos.obtener_proxies_dict()` prioriza Tor; rotación antes de cada dork con pausa 20-40s; máximo 1 petición Tor simultánea; storage_state persistente entre sesiones.

## Runner de verificación

`probar_dorks.py` (raíz del proyecto): ejecuta los 3 scrapers de dorks con:
- Máx 3 reintentos cada módulo.
- Rotación Tor entre intentos (`tor_utils.rotar_tor`).
- Display de estado (Tor ✓/✗, tiempo, resultados).
- Resumen final (scrapers_ok, total_eventos, total_grupos, total_organizadores, tiempo_total).

## Estado real de SERP vía Tor (agosto 2026)

Tests directos con Tor SOCKS5:

| Motor | Status vía Tor | Resultado |
|-------|---------------|-----------|
| **DDG (html.duckduckgo.com)** | ✅ 200 | **Funciona perfecto** — 10 resultados FB por query. Parser `result__a` + `uddg=`. Motor principal. |
| **Google** | 429 | Rate-limited. Playwright+stealth puede funcionar intermitentemente. No confiable. |
| **Bing** | 200 | Retorna basura (sitios no relacionados). Útil para Tor. |
| **Startpage** | 200 | Challenge anti-bot (JavaScript). No funcional vía requests. |
| **SearxNG** | 429/HTML | Instancias públicas bloquean Tor o devuelven HTML en vez de JSON. |
| **Facebook directo** | 200 | Página genérica carga, pero páginas específicas → login wall. Playwright+Tor no puede extraer datos de páginas FB individuales. |

**Conclusión**: DDG es el único SERP confiable vía Tor. `facebook_dorks.py` ya lo usa correctamente. No hay forma gratis de obtener más SERP vía Tor sin IP del usuario.

## Módulos nuevos (agosto 2026)

- `core/serp_tor.py`: Interfaz centralizada para SERP. Usa DDG+SearxNG vía Tor. `buscar_serp(query)` y `buscar_serp_fb(subgenero, localidad)`.
- `core/plugin_loader.py`: Auto-descubre scrapers en `scrapers/*.py`. Sin tocar `orquestador.py` para añadir nuevos.
- `scrapers/fb_playwright_tor.py`: Playwright+Tor para páginas FB. Registrar pero desactivado (login wall).