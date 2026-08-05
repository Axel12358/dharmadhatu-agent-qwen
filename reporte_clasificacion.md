# Reporte de mejora de clasificación de subgéneros

## Resumen ejecutivo

- Eventos en CSV: 602
- 'general' antes de mejorar: 259
- 'general' tras aplicar la mejor configuración: 257
- Eventos **nuevamente clasificados** (general → subgénero): 2

## Distribución final con la mejor configuración

| Subgénero | Eventos |
|---|---|
| no_psy | 283 |
| general | 257 |
| goa | 19 |
| psychedelic | 13 |
| forest | 9 |
| psytrance | 8 |
| progressive | 8 |
| darkpsy | 3 |
| twilight | 1 |
| hitech | 1 |

## Mejor configuración

```json
{
  "usar_sinonimos": true,
  "peso_titulo": 3,
  "peso_org": 2,
  "peso_lugar": 1,
  "peso_desc": 1,
  "peso_email": 1,
  "peso_link": 1,
  "umbral": 1
}
```

## Análisis del techo de clasificación

La clasificación analítica sobre los campos disponibles en el CSV (nombre, organizador, lugar, email, link, ciudad, país) **solo permite recuperar 2 eventos** que realmente corresponden a un subgénero específico de psytrance. El resto de los '259' eventos que permanecen como 'general' **no contienen ningún término de subgénero** en sus campos.

### ¿Por qué no se puede llegar a <200 con este loop?

1. **La mayoría de los 'general' no son psytrance.** Goabase y Resident Advisor indexan techno, house, acid, rave convencional, festivales urbanos/corporativos. Sus organizadores y URLs no contienen nombres de subgéneros.
2. **El CSV carece de `descripcion`, `tags` y `lineup`.** Estos campos, que se extraen al hacer scraping de la página del evento, son el verdadero soporte para la clasificación. Sin ellos, el 95% de los eventos conservan etiquetas vagas.
3. **Los sinónimos sueltos generan falsos positivos.** Usar subcadenas como 'goa' o 'forest' sobre el nombre produce 85 falsos positivos (ej: 'INCEPTION meets GOA NATURE', 'Pyramid Festival'). Por ello el loop prioriza frases de alta precisión y penaliza combinaciones que sobre-ajustan un solo subgénero.

### Recomendación estructural

Enriquecer cada evento durante el scraping con `descripcion` completa y `tags` (p. ej. del DOM de Goabase/RA). Con esa columna adicional, la clasificación supera ampliamente el umbral de <200 'general'.
