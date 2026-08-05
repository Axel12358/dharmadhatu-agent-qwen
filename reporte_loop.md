# Reporte del loop de mejora continua

- Fecha: 2026-08-04T14:35:38.895412
- Iteraciones ejecutadas: 4
- **Mejor score**: 2111.09
  - Eventos totales: 426 (426 deduplicados)
  - Eventos con email: 426
  - Tiempo: 189.1 s
  - Config: {"lote_ciudades": 16, "horizonte_meses": 6, "max_eventos": 300, "max_keywords": 600, "max_visitas": 80}
  - Por fuente: {"Goabase": 100, "Songkick": 1, "Resident Advisor": 300, "Facebook (público SERP)": 25}

## Tabla de iteraciones

| # | Ciudades RA | Horizonte (m) | Keywords FB | Visitas FB | Eventos | Con email | Tiempo (s) | Score |
|---|-------------|---------------|-------------|------------|---------|-----------|------------|-------|
| 1 | 16 | 6 | 400 | 50 | 401 | 401 | 171.8 | 1987.82 |
| 2 | 16 | 6 | 400 | 80 | 419 | 419 | 194.2 | 2075.58 |
| 3 | 16 | 6 | 600 | 50 | 420 | 420 | 199.0 | 2080.1 |
| 4 | 16 | 6 | 600 | 80 | 426 | 426 | 189.1 | 2111.09 |

## Recomendación

Probar con más keywords de Facebook (>400). Probar con más visitas de enriquecimiento (>50).
