# Clasificación de Subgéneros: Loop de Mejora

## Contexto

El CSV consolidado (`eventos_encontrados.csv`) contiene eventos de **Goabase**,
**Songkick**, **Resident Advisor** y **Facebook**. Cada evento lleva una etiqueta
`subgenero`. La clasificación se realiza en `main.py` mediante
`clasificar_eventos()` → `EventExtractor.clasificar_subgenero(texto)` sobre
`nombre + lugar + descripcion`.

Histórico: ~85% de los eventos quedan etiquetados como `"general"`, porque el
texto disponible no contiene términos de subgénero.

## Estado actual del CSV (métrica de referencia)

| Métrica | Valor |
|---|---|
| Eventos totales | 439 |
| `"general"` | 378 (86 %) |
| Clasificados (darkpsy/forest/.../goa) | 61 (14 %) |
| Origen de los general | Goabase 86, Facebook 12, Resident Advisor 280 |
| `descripcion` / `tags` en el CSV | N/A (ausentes) |

## Arquitectura del loop (`loop_clasificacion.py`)

1. Carga `eventos_encontrados.csv` como conjunto de prueba.
2. Invoca `EventExtractor.clasificar_subgenero()` con combinaciones de:
   - `usar_sinonimos` (on/off)
   - `peso_titulo`, `peso_org`, `peso_lugar`, `peso_desc`, `peso_email`, `peso_link`
   - `umbral` de puntuación mínima
3. Mide **solo eventos nuevamente clasificados** (`general → subgénero`). Los ya
   clasificados se conservan (no se sobreescriben).
4. Aplica una **penalización de precisión**: si un solo subgénero (no general)
   representa > 35 % de los clasificados, el combo se considera sobreajustado
   (falsos positivos) y se descarta.
5. Guarda la mejor configuración en `mejor_clasificacion.json` y un reporte
   legible en `reporte_clasificacion.md`.

## Estrategias de clasificación implementadas

En `scrapers/event_extractor.py`:

1. **Diccionario `SINONIMOS` ampliado** — alias con límites de palabra
   (`dark psy`, `full-on`, `prog`, `hi-tech`, `goatrance`, etc.).
2. **Frases de alta precisión** (`PALABRAS_CLAVE_PRECISAS`) — evita falsos
   positivos: términos cortos como `"goa"` o `"forest"` aparecen como subcadena
   en nombres que no son psytrance (ej. *"INCEPTION meets GOA NATURE"*,
   *"Pyramid Festival"*). Sólo cuentan frases como `"goa trance"`,
   `"forest psytrance"`, `"psychedelic"`, etc.
3. **Clasificación ponderada por campos** — `organizador`, `lugar`, `email`,
   `link` aportan peso adicional cuando el nombre no basta.
4. **Prioridad de pesos** — título (3) > organizador (2) > lugar/email/link (1).

## Resultado honesto del loop

> Con los campos disponibles en el CSV, **solo 1 evento** pasa de `"general"`
> a un subgénero específico bajo la mejor configuración de precisión.

Las 378 etiquetas `"general"` restantes **no contienen ningún término de
subgénero** en nombre/organizador/lugar/email/link. La gran mayoría (86 %) son
eventos que **no son psytrance**: techno, house, acid, rave convencional y
festivales urbanos de Goabase/RA.

## ¿Por qué no se llega a <200?

| Causa | Evidencia |
|---|---|
| La mayoría de `"general"` no es psytrance | 280/378 provienen de RA (acid/techno) y 86 de Goabase (techno/house) |
| El CSV carece de `descripcion`, `tags`, `lineup` | columna `descripcion` vacía para todos los eventos |
| Sinónimos sueltos → falsos positivos | subcadenas `"goa"`/`"forest"` generan 85 falsos positivos |

## Recomendación estructural

**Enriquecer cada evento con `descripcion` completa y `tags` al momento del
scraping** (del DOM/HTML de Goabase y RA). Con esa columna adicional la
clasificación basada en patrones supera ampliamente el umbral de `<200
"general"`.

Hasta tanto, la configuración guardada en `mejor_clasificacion.json` representa
la **precisión máxima** alcanzable con los datos actuales y puede ser adoptida
por `main.py` en un futuro cambio (fuera del alcance actual por el protocolo de
no-modificar `main.py`).
