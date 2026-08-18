# AGENTS.md — Estado de trabajo (handoff)

> Documento de continuidad: cualquier modelo que retome el trabajo DEBE leer esto
> antes de tocar nada. Describe protocolo, estado actual, cambios pendientes sin
> commitear y siguientes pasos.

## PROTOCOLO (obligatorio, no negociable)

- **NO tocar** `goabase.py`, `songkick.py`, `main.py`.
- **Siempre sumar, nunca restar** (aditivo, no destructivo).
- **Todo local, gratuito, sin login** (nada de APIs de pago, nada de credenciales).
- **Documentar todos los cambios en markdowns** (`docs/*.md`).
- Los cambios deben ser reversibles.
- Solo commitear cuando el usuario lo pida explícitamente.
- Al terminar cada tarea: `python3 -m py_compile <archivos>` para verificar sintaxis.

## OBJETIVO GENERAL

Bot que consolida un CSV limpio de eventos psytrance reales desde múltiples fuentes.
Meta Fase 3: **400+ eventos totales** y **80-100 eventos de Facebook** con fecha,
lugar y organizador reales. ✅ CUMPLIDA.

## ESTADO ACTUAL (último CSV generado)

`eventos_encontrados.csv` / `eventos_psytrance.csv` = **420 eventos**:

- Goabase: 301
- Resident Advisor: 17
- Facebook (público SERP): 101
- Facebook (Darkpsy Family): 1
- Total Facebook: **102**

Subgéneros: psytrance 343, goa 19, forest 18, darkpsy 17, psychedelic 12,
progressive 8, twilight 1, hitech 1, psychill 1.

Los CSV están en `.gitignore` (no se commitean).

## CAMBIOS REALIZADOS EN LA FASE 3 (pendientes de commitear)

Ejecutados y validados (el CSV actual ya refleja los resultados).

### 1. `run_facebook_only.py`
- 3 pasadas por ejecución (`PASADAS = 3`).
- `max_keywords=30`, `max_visitas=60`, `timeout=1200` por pasada.
- Importa `clasificar_eventos` y `limpiar_calidad` de `main_fuentes`.
- **Nunca resta**: los eventos ya existentes del CSV se conservan tal cual;
  solo los NUEVOS se clasifican y filtran (`limpiar_calidad`), y luego dedup.
- Al final imprime desglose por fuente y subgénero + total.

### 2. `scrapers/facebook_mcp.py`
- `MAX_EVENTOS_POR_RUN = 80`: techo de eventos nuevos por pasada (antes 50).
- `_enriquecer_fechas_publicas()`: si la visita anterior en caché no logró
  **organizador**, reintenta la visita (antes saltaba por caché). Esto permite
  que eventos FB sin fecha pasen `limpiar_calidad` (exige link+organizador).
- `_via_grupos_conocidos()`: 60 grupos por ejecución (antes 30), con 90s de
  timeout interno en lugar de `TIMEOUT_PER_STRATEGY` (20s).
- `_normalizar_fecha_publica()`: ahora hay UNA sola definición (se eliminó un
  duplicado que pisaba la mejorada); acepta "15 Aug 2026", "Aug 15, 2026",
  "Aug 15", "Friday, August 15 at 10:00 PM", etc. (meses abreviados en
  `meses_es`/`meses_en`).

### 3. `config_grupos.json`
- 66 países sin duplicados; ciudades ampliadas en no-europeos (Brasil 12,
  México 11, Argentina 10, Chile 8, Colombia 9, Japón 8, Australia 9,
  Sudáfrica 7, Israel 7, Turquía 8, India 9, etc.).

### 4. `recuperar_fb_cacheados.py` (NUEVO)
- Recupera eventos que quedaron en `eventos_visitados.json` con organizador
  pero fuera del CSV (se perdieron antes del fix de re-visita).
- Re-visita cada URL vía Playwright (FB bloquea requests simples), extrae
  nombre/fecha/lugar/organizador y consolida. Aditivo. Recuperó 53 eventos.

## PRÓXIMOS PASOS (en orden)

1. ~~Actualizar `docs/fuentes_psytrance.md` con los resultados de Fase 3~~ ✅ hecho.
2. Commitear los cambios de Fase 3 (cuando el usuario lo pida):
   `main_fuentes.py`, `run_facebook_only.py`, `scrapers/facebook_mcp.py`,
   `recuperar_fb_cacheados.py`, `config_grupos.json`, `AGENTS.md`, docs.
3. Opcional: seguir lanzando `run_facebook_only.py` para acumular más volumen.

## FASE 4 — LOOP CENTRAL DE OPTIMIZACIÓN (en progreso)

Nueva capa de orquestación **aditiva** con dedup GLOBAL (no depende solo del CSV).

- `core/orquestador.py`: `orquestar_scrapers()` — paralelo (ThreadPool), timeouts
  por scraper, recolección + dedup global, escritura aditiva de
  `eventos_encontrados.csv`, métricas en `metricas_orquestador.json`.
  Acepta `activos=[...]` y `dry_run=True`.
- `core/deduplicador.py`: `Deduplicador` (hash SHA-256 nombre+fecha+desc),
  `cache_dedup.json`, `filtrar_nuevos()`, `registrar_vistos()`, auto-limpiar >5000.
- `core/recursos.py`: rotación UA + proxies via `obtener_user_agent()`/
  `obtener_proxy()`/`obtener_proxies_dict()` (proxies.txt + anti_block).
- `main.py` (solo aditivo al final): llamada condicional a `core.orquestador`.

**ADVERTENCIA/MÓDULOS SOMBRA**: `scrapers/instagram` es un PAQUETE que reexporta
`scrape_instagram_events` desde `scrapers/instagram_scraper.py`. Existe también
un módulo `scrapers/instagram.py` (y `instagram_fusion.py`) que el paquete SOMBRA.
Cuando se importe `from scrapers.instagram import ...`, gana el paquete → usa
`instagram_scraper.py`. Por eso la adaptación Fase 4 se hizo en
`instagram_scraper.py` (firma ahora `(config, timeout=None, deduplicador=None)`).
`scrapers/instagram.py` recibió edits PARALELOS pero es código muerto; no gatear
su uso al llamar `scrapers.instagram_scraper` directamente para evitar ambigüedad.

### Estado Fase 4 (validado)
✅ Deduplicador funciona; primera corrida real añadió 6 eventos (psytrance.pl)
   420 → 426 CSV; `cache_dedup.json`=426; 2ª corrida = 0 nuevos (dedup activo).
✅ `core/recursos.py` compila y rota (59 UAs / 300 proxies).
✅ Orquestador: dry-run y corrida real de una fuente funcionan; run completo
   tarda >120s (Facebook hasta 900s) — requiere timeout de shell mayor.
✅ `main.py` recompila con la llamada condicional.
⚠️ Documentación Fase 4 añadida a `docs/fuentes_psytrance.md` (fecha actual).
⚠️ Pendiente: commitear (solo si el usuario lo pide) y un run completo.

## FASE 4b — COMPLETAR N/A DE FACEBOOK (hecho)

`scrapers/completar_fb_na.py` (NUEVO, aditivo) — `completar_eventos_fb()`.
Visita con Playwright sync + stealth (user-agent móvil, sin cookies) las URLs de
eventos FB con campos N/A en `fecha`/`lugar`/`organizador` y los completa.
- 5 workers paralelos, 2 intentos/URL, 20s/página.
- Solo completa campos N/A; nunca pisa datos; campo irresoluble → se deja.
- Normaliza fechas a ISO. Un año suelto en un título NO cuenta como fecha
  (evita inventar "2026-01-01").
- m.facebook.com NO expone `data-testid` → el parseo usa el texto visible por
  líneas (h1 real saltando "Este navegador no es compatible", fecha por línea
  candidata, lugar de "fecha | hora | lugar" o dirección, organizador ES/EN).
- Salidas: CSV (mismo orden de columnas) + `eventos_completados.json`.
- Integrado al final de `main.py` (llamada opcional, comentario
  "Completar N/A de Facebook").

### Resultado Fase 4b (validado)
✅ 45 candidatos → 30 completados; 16 siguen incompletos (11 solo por `lugar`).
✅ 37 filas FB mejoradas; 0 datos perdidos (457 filas intactas).
✅ Idempotente: 2ª corrida no degrada nada.
✅ Prueba del spec OK:
   `source venv/bin/activate && python3 -c "from scrapers.completar_fb_na import completar_eventos_fb; completar_eventos_fb()"`.
⚠️ `playwright_stealth` en este venv NO tiene `stealth_sync`; se usa
   `Stealth().use_sync(sync_playwright())` o `apply_stealth_sync(page)`.

## PROBLEMAS CONOCIDOS

- **Reddit**: 429 rate-limit tras ~2 peticiones; con timeout 60s se omite.
- **Meetup**: 0 eventos psytrance reales (timeout 60s).
- **Eventbrite**: timeout 90s sin eventos psytrance.
- **IsraTrance**: sitio caído.
- **Tor**: en algunos runs `net::ERR_SOCKS_CONNECTION_FAILED`, cae a conexión
  directa (funciona, solo añade ~20s).
- **Facebook sin cookies**: los grupos privados no rinden; la estrategia
  productiva es SERP público + grupos públicos.
- Fecha en español abreviada ("15 Ago 2026") → None: "ago" no está en la
  alternancia de nombres de mes (solo español completo e inglés abreviado).
- Tras muchos runs la SERP satura (reencuentra los mismos eventos); el dedup
  contra el CSV apenas añade +1-4 por run. `recuperar_fb_cacheados.py` fue la
  palanca que llevó FB de 50 a 102.

## ARCHIVOS RELEVANTES

- `main_fuentes.py`: orquestador. `clasificar_eventos()` (4 pasadas),
  `limpiar_calidad()`, `ejecutar_fuentes_nuevas()` (incluye Facebook 12/20/900).
- `run_facebook_only.py`: script standalone Facebook (3 pasadas, 30 keywords,
  60 visitas) + consolidación aditiva con CSV.
- `recuperar_fb_cacheados.py`: recupera eventos FB cacheados perdidos (Playwright).
- `scrapers/facebook_mcp.py`: `scrape_facebook_events()`, SERP en 5 fases,
  `_normalizar_fecha_publica()`, `_enriquecer_fechas_publicas()`.
- `scrapers/event_extractor.py`: `clasificar_subgenero()`, `SINONIMOS`.
- `utils/helpers.py`: `deduplicar_eventos()`, `limpiar_eventos()`.
- `config_grupos.json`: 66 países con ciudades.
- `docs/fuentes_psytrance.md`: arquitectura y resultados.
- `README.md`: resumen del proyecto.

## COMMITS RECIENTES

- `c85f05c` feat: script Facebook standalone + 15 eventos FB (de 8 a 15)
- `ad480db` docs: actualizar resultados - 326 eventos
- `2e6ac0c` fix: Facebook timeout 300s → 600s (10 min) para 15 keywords
- `9a79c61` feat: Facebook expandido (15 keywords, 50 visitas, 300s timeout)
- `6fa3df9` fix: integración Facebook + descripción Goabase

Los cambios de Fase 3 NO están commiteados.
