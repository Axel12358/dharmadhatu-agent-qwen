---
name: fb-organizer-osint
description: Use when enriching or mining organizadores (organizers), emails, géneros o geografía de eventos de psytrance/RA/Goabase almacenados en eventos_encontrados.csv. Cubre el barrido SERP por ciudad, el crawl de canales públicos de Telegram (t.me/s), el relleno local de N/A y los límites operativos (Tor obligatorio, lock de escritura). Suena con keywords como "organizador", "sin organizador", "email", "N/A", "rellenar", "cobertura", "minar organizador", "Telegram", "venue", "subgenero".
---

# FB/eventos OSINT: organizadores, emails y campos N/A

Metodología validada para completar `eventos_encontrados.csv` en el proyecto
dharmadhatu (y réplicas con el mismo esquema: nombre, fecha, lugar, pais,
continente, subcontinente, fuente, organizador, email, link, subgenero,
tipo_lugar).

## Reglas no negociables

1. **Todo tráfico vía Tor**: proxy `socks5h://127.0.0.1:9050` (ControlPort
   9051 para NEWNYM). Nunca IP real, ni cuentas, ni pagos, ni APIs de pago
   (Apify/Meta Content Library quedan fuera).
2. **Toda escritura al CSV bajo `core.csv_lock.csv_locked_rows(CSV_FILE)`**
   (fcntl.flock). Sin eso, los escritores concurrentes (orquestador, loop,
   venue_enricher, mcp_clasificar) se pisan la información.
3. Los 3 jobs persistentes viven en
   `~/Library/LaunchAgents/com.dharma.{loop,orquestador,enricher}.plist`
   (bash wrappers `run_*_completar.sh`). Tras solo editar código del loop,
   recargar con `launchctl kickstart -k gui/$(id -u)/com.dharma.loop`.
4. Los MCP de clasificador usan merge bajo lock:
   `core/mcp_clasificar.py` → `_guardar_merge_subgenero`.

## Flujo de enriquecimiento (cascada)

- **Rama A (eventos CON organizador)**: venue → email (venue_enricher),
  contacto del organizer vía su web; rellenar país/continente/subgénero.
- **Rama B (eventos SIN organizador, pool ~1,460 FB/IG)**: descubrir organizer
  por 4 vectores, en orden de ROI:
  1. Telegram (`t.me/s/<canal>` previews sin login; mensajes traen URL FB +
     organizador).
  2. Dorks de grupos FB por ciudad/subgénero/año.
  3. SERP por ciudad (el que más produce).
  4. Goabase venue reverse-lookup.

## Vectores que SÍ funcionan

### 1. Barrido SERP por ciudad
`loop_completar.py::minar_organizadores_fb_serp`

- Dork: `site:facebook.com/events <ciudad> <subgenero> <año>` vía DDG.
- El snippet `Event in X by ORGANIZER on <día>` + URL exacta del evento.
- Match por URL literal normalizada (`_normalizar_link_fb`); acierto ~2-6
  nuevos/mejorados por ciudad. Rotar ciudades en checkpoint
  `fb_ciudades_pendientes`.
- Los eventos FB (matriz) usan fecha fake `YYYY-01-01`: **match solo por año**,
  nunca por fecha.
- `es_org_slugderivado()`: sobrescribe organizadores basura derivados del slug
  ('Ph Hs') con el real; de aquí salen los "mejorados".

### 2. Telegram (canales públicos)
`loop_completar.py::minar_organizadores_telegram`

- Descubrir canales: `site:t.me <ciudad> psy trance events` (DDG).
- Crawl `https://t.me/s/<canal>` (preview paginable, funciona vía Tor; no es
  necesaria la app). Extraer `tgme_widget_message_text` y regex de URLs FB:
  `https?://(?:www|m|mbasic)\.facebook\.com/events/[...]`.
- Organizador: patrón `by X on` / `presented by X` / `organized by` dentro del
  mismo mensaje (`_org_desde_texto`).
- Un canal activo cubre docenas de eventos del mismo promoter; limitar a 1-2
  ciudades por iteración.

### 3. Rama A: email por organizador (promoter/label)
`loop_completar.py::minar_email_organizador` + `_aplicar_email_organizador`

- DDG `"<org>" <lugar> email contact` / `"<org>" psytrance promoter email booking`
  (3 queries en paralelo, snippets + fetch de página, misma lógica que
  `buscar_email_para_evento`).
- Se busca UNA vez por promoter y se aplica a TODOS sus eventos sin email
  (bajo lock). Cola rotativa `org_email_pendientes` en el checkpoint:
  3 por iteración, máx 3 intentos antes de abandonar.
- Siempre revalidar con `es_email_valido` (filtra placeholders, dominios
  bloqueados y agencias).

### 4. Relleno local de N/A (cero red)
`core/rellenar_na.py` → `rellenar_na_locales(rows)`

- `lugar → pais`, `pais → continente/subcontinente` (mapas auto-aprendidos del
  dataset + `geo_cache.json`).
- `subgenero`: clasificador por keywords de `nombre` mapeado al vocabulario
  existente (psytrance, progressive, goa trance, dark psy, forest, hitech,
  psycore, psybient, twilight, fullon...).
- Resultado típico: cont/subcont 1,363→449, pais 501→392, subgenero 610→0.
- Ejecutar periódicamente desde el loop (cada 25 iteraciones).

## Vectores muertos (no reintentar)

- Facebook logged-out (www/mbasic/touch): consent wall por reputación IP de
  Tor; HTML inestable; sin hosts embebidos.
- Dork por ID de evento (`site:facebook.com/events/756`): no indexa.
- searxng vía Tor: 0 resultados; usar solo DDG (`core/serp_tor.py`).
- Cruce RA/Goabase por nombre (exacto/laxo): 0; cruce por slug-venue: basura.
- Nitter, Discogs, picuki, Wayback/CDX/CommonCrawl, RDAP: muertos/403/JS-shell.
- Apify no-login actors y Meta Content Library: fuera de restricciones.

## Limpieza de organizadores

`_limpiar_org_fb(org)`: quita and/&/@/\ /store/hotel y organizadores ≤5
palabras o genéricos. Filtrar junk con `es_nombre_gen_org()` y dominios de
agencias/plataformas en la lista de bloqueo (nunca colar emails de
AGENCY/PLATFORM).

## Métricas de referencia (línea base)

- Total ~5,976 eventos. FB (matriz) 3,857 → organizador ~62% (techo realista
  vía Tor ~68-72%). RA 1,198 → email válido ~84.5%. Goabase 100% email.
- `estado_dataset` del MCP clasificador da la cobertura en vivo.

## Convenciones de código

- Sin comentarios explicativos innecesarios en el código nuevo.
- Escritores SIEMPRE vía `csv_locked_rows`; jamás reescribir el archivo entero
  con snapshot stale (causa histórica de pérdidas de datos).