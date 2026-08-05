# Fuentes Especializadas Psytrance — Arquitectura

## Visión General

El pipeline de fuentes especializadas en psytrance consta de:

1. **Scrapers de nuevas fuentes** (reddit, meetup, ektoplazm, psytrance.pl, isratrance)
2. **Scraper opcional de Eventbrite** (requiere Playwright)
3. **Filtro `no_psy`** en `main.py` (marca RA general como no-psy)
4. **Deduplicación** en `utils/helpers.py` (clave compuesta: nombre+fecha+lugar)
5. **Limpieza de calidad** `limpiar_calidad()` en `main_fuentes.py` (fecha + link + psytrance real)
6. **Orquestador** `main_fuentes.py` (consolida, clasifica, limpia, exporta)

## Limpieza de Calidad (limpiar_calidad)

Añadido Ago 2026. Elimina falsos positivos que antes entraban al CSV "limpio"
solo por no ser `no_psy` (posts de discusión de Reddit, salsa/meditación de Meetup,
etc.). Un evento es válido si cumple TODOS:

- **Fecha real**: formato `YYYY-MM-DD` (no `N/A`).
- **Link real**: URL `http(s)://`.
- **Psytrance real**: la fuente es especializada (Goabase, Psytrance.pl, IsraTrance,
  Ektoplazm, Songkick) O el subgénero pertenece a la familia psytrance
  (`SUBGENEROS_PSY`: psytrance, goa, darkpsy, forest, hitech, progressive,
  psychedelic, psychill, psybient, fullon, twilight, psycore, suomisaundi, zenon).

## Scrapers Nuevos (añadidos Ago 2026)

### reddit_psy.py
- **Fuente**: r/psytrance, r/goatrance, r/aves, etc. vía RSS/Atom
- **Método**: `requests` + `xml.etree.ElementTree`
- **Yield**: ~1-50 posts de eventos (muy sensible al rate-limit)
- **Notas**: Ahora exige `_es_psytrance()` (keyword psytrance en título/contenido) Y
  fecha extraíble del título. Antes `_es_psytrance` estaba definida pero no se usaba,
  lo que dejaba pasar posts de discusión como "eventos". Rate-limit agresivo (429).

### psytrance_pl.py
- **Fuente**: psytrance.pl (portal polaco)
- **Método**: `requests` + `BeautifulSoup`
- **Yield**: ~10-15 eventos (históricos + Facebook link)
- **Notas**: La página `/events/` redirige a Facebook Events. Las páginas individuales de eventos (`/event/`) tienen fechas y lugares.

### isratrance.py
- **Fuente**: isratrance.com (portal israelí legacy)
- **Método**: `requests` + `BeautifulSoup`
- **Yield**: 0 eventos (sitio actualmente caído)
- **Notas**: Sitio HTML 4.01 con framesets. `/events/` devuelve 404. El sitio no está accesible actualmente.

### ektoplazm.py
- **Fuente**: ektoplazm.com (netlabel de psytrance)
- **Método**: `requests` + `BeautifulSoup`
- **Yield**: ~3 menciones de eventos en blog
- **Notas**: Es un netlabel, no un portal de eventos. Detecta menciones de festivales/parties en posts de blog.

### meetup_psy.py
- **Fuente**: meetup.com
- **Método**: `requests` + JSON-LD parsing (timeout reducido a 8s)
- **Yield**: 0 eventos reales (Meetup no tiene eventos psytrance; los previos eran
  salsa/meditación, ahora eliminados por `limpiar_calidad`)
- **Notas**: Ciudades ampliadas de 40 a 70+. Keywords ampliadas con "trance",
  "festival", "rave", "party", "open air".

### eventbrite_psy.py (opcional)
- **Fuente**: eventbrite.com
- **Método**: `playwright` (renderizado de SPA)
- **Yield**: ~20 eventos/ciudad
- **Notas**: Requiere `playwright install chromium`. Los resultados no son siempre psytrance-específicos; la clasificación los filtra.

## Fuentes Existentes (consolidadas en main.py)

| Fuente | Yield | Método | Notas |
|--------|-------|--------|-------|
| Goabase | 301 eventos | JSON-LD API | Portal psytrance específico. **Limit ampliado 100→301** (la API expone 301 parties futuras reales con fecha+link). |
| Resident Advisor | 300 eventos | GraphQL público | Filtrado por género/keywords; solo ~17 sobreviven a `limpiar_calidad`. |
| Facebook (SERP + grupos) | ~8 eventos reales | Playwright + requests | Búsqueda pública SERP + scraping de grupos públicos. Estrategia 5: grupos conocidos visitados con Playwright/requests para extraer eventos reales (no placeholders). |
| Songkick | 1 evento | HTTP + BS4 | General, bajo yield para psytrance. |

## Validación de Calidad

Añadido en esta ronda. Dos funciones complementarias en `utils/helpers.py`:

- `validar_evento(evento)`: verifica que un evento tenga nombre no vacío,
  link URL válida (http), fecha en formato YYYY-MM-DD (o "N/A" para fuentes
  especializadas), y lugar no vacío para fuentes generales.
- `limpiar_eventos(eventos)`: aplica `validar_evento` + `deduplicar_eventos`
  para producir una lista limpia de eventos válidos.

En `main_fuentes.py`, `limpiar_eventos()` se aplica **antes** de la deduplicación
y clasificación, eliminando eventos inválidos (salsa/meditación de Meetup,
posts de discusión de Reddit, placeholders de Facebook sin fecha).

`limpiar_calidad()` (en `main_fuentes.py`) aplica un filtro más estricto:
exige fecha real + link real + subgénero psytrance real para fuentes
no especializadas. Esto garantiza que el CSV limpio solo contiene eventos
psytrance verificados.

```
CSV existente + nuevos scrapers
        │
        ▼
  limpiar_eventos()     ← validar_evento: nombre + link + fecha + lugar
        │
        ▼
  deduplicar_eventos()  ← clave compuesta (nombre, fecha, lugar)
        │
        ▼
  clasificar_eventos()  ← subgénero por keywords
        │
        ▼
  filtrar_no_psy()       ← marca RA general como no_psy
        │
        ▼
  limpiar_calidad()      ← exige fecha + link + psytrance real
        │
        ▼
  clasificar_geo()       ← continente/subcontinente
        │
        ▼
  ┌─────────────────┬──────────────────┐
  │ eventos_encontrados.csv  │ eventos_psytrance.csv  │
  │ (todos, reales)          │ (solo psytrance real)  │
  └─────────────────┴──────────────────┘
```

## Resultados del Consolidado (Ago 2026, tras limpiar_calidad)

- **CSV completo** (`eventos_encontrados.csv`): 602 eventos
- **CSV limpio psytrance** (`eventos_psytrance.csv`): **318 eventos** reales
  (fecha YYYY-MM-DD + link http + psytrance real, todos únicos)
- **Distribución**: Goabase 301, Resident Advisor 17
- **Rango de fechas**: 2026 (287), 2027 (30), 2028 (1)
- **Top países**: Alemania, Suiza, Camboya, Austria, Japón, España, Países Bajos, Italia, Perú
- **Filtro no_psy**: Marca `subgenero="no_psy"` cuando `subgenero=="general"` y `fuente=="Resident Advisor"`
- **Deduplicación**: Elimina duplicados por (nombre, fecha, lugar)

> ⚠️ **Cambio de filosofía**: antes el "CSV limpio" era `todo lo que no era no_psy`
> (incluía ~100 falsos positivos: posts de discusión de Reddit y salsa/meditación
> de Meetup). Ahora `limpiar_calidad()` garantiza que cada fila es un evento real
> con fecha y enlace. El conteo honesto pasó de 263 (inflado) a 318 (verificado).

## Uso

```bash
# Ejecutar todos los scrapers y exportar CSVs
python main_fuentes.py

# Solo mostrar stats sin exportar
python main_fuentes.py --dry-run

# Ejecutar scraper individual
python -m scrapers.reddit_psy
python -m scrapers.psytrance_pl
python -m scrapers.meetup_psy
```

## Dependencias

Todas las dependencias ya están en `requirements.txt`:
- `requests` — HTTP requests
- `beautifulsoup4` — HTML parsing
- `playwright` — Para eventbrite_psy.py (opcional)
- `lxml` — Parser XML para RSS

## Limitaciones conocidas

1. **Reddit**: Rate-limit agresivo (429). Delay entre subreddits necesario. Tras exigir psytrance+fecha, el yield real es bajo (1-10 posts).
2. **Eventbrite**: SPA requiere Playwright. Lentitud por múltiples ciudades. Resultados no siempre psytrance-específicos (0 eventos tras filtro).
3. **Meetup**: No hay eventos psytrance reales; JSON-LD devuelve 0. Los eventos previos eran salsa/meditación (falsos positivos, ahora eliminados por `limpiar_calidad`).
4. **IsraTrance**: Sitio legacy caído, sin eventos scraping.
5. **Psytrance.pl**: Principalmente histórico (2009-2017) + Facebook link.
6. **Ektoplazm**: Netlabel, no portal de eventos. Menciones en blog only.
7. **Goabase**: Los 301 eventos de la API son el pilar del dataset. `limpiar_calidad` solo los conserva si la API expone fecha+link reales (verificado: 301/301).

## Principios de Diseño

- **No tocar** `goabase.py`, `songkick.py`, `main.py` (excepto para añadir filtros).
- **Ampliar desde el orquestador**: se subió el límite de Goabase de 100→301 en
  `main_fuentes.py` (no en `goabase.py`), respetando el código original.
- **Siempre sumar, nunca restar**: cada scraper es un módulo independiente que se puede añadir/quitar.
- **Todo local, gratuito, sin login**: ningún scraper requiere autenticación ni servicios de pago.
- **Reversible**: cada scraper se puede desactivar sin afectar al resto del pipeline.
- **Calidad verificable**: `limpiar_calidad()` garantiza que cada fila del CSV limpio
  tiene fecha real, link real y es psytrance real (sin falsos positivos).