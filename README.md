# Dharmadhatu Bot v5

## Descripción

El Dharmadhatu Bot v5 es un orquestador de eventos que busca eventos de psytrance y subgéneros relacionados de múltiples fuentes (Goabase, Songkick, Resident Advisor, Facebook, Instagram) y los consolida en un solo CSV con una clasificación enriquecida por subgénero y ubicación geográfica.

## Características

### Extracción Multi-fuente
- **Goabase**: API JSON directa (301 eventos, limit ampliado)
- **Songkick**: API GraphQL (1 evento)
- **Resident Advisor**: API GraphQL sin login (~20 psytrance tras limpieza)
- **Facebook**: Búsqueda pública SERP + grupos públicos (Playwright + requests)
- **Reddit**: RSS de subreddits psytrance (rate-limit 429, filtrado estricto)
- **Meetup**: JSON-LD + HTML parsing (40+ ciudades, keywords ampliadas)
- **Psytrance.pl**, **Ektoplazm**, **IsraTrance**: scrapers especializados

### Calidad y Validación
- `limpiar_eventos()`: valida fecha, link y nombre antes de clasificar
- `limpiar_calidad()`: filtra eventos sin fecha real o link válido, y subgéneros no-psy de fuentes generales
- `deduplicar_eventos()`: elimina duplicados por (nombre, fecha, lugar)
- `filtrar_no_psy()`: marca RA general como no_psy

### Clasificación de Subgénero
Los eventos se clasifican automáticamente en los siguientes subgéneros específicos:
- `darkpsy`
- `forest`
- `psychill`
- `psybient`
- `fullon`
- `progressive`
- `goa`
- `hitech`
- `twilight`
- `psycore`
- `suomisaundi`
- `zenon`
- `psychedelic`
- `psytrance`

**Reclasificación Goabase**: Los eventos de Goabase con subgénero `"general"` se reclassifican automáticamente como `"psytrance"` (Goabase es un portal psytrance por definición). Esto eliminó 259 eventos `"generales"` del conteo.

**Organizador recurrente**: Si un organizador tiene ≥2 eventos clasificados con un subgénero psytrance real, sus eventos `"general"` heredan ese subgénero.

**Diccionario de sinónimos ampliado**: Se añadieron variantes como `"dark-psy"`, `"forest-psy"`, `"prog"`, `"hi-tech"`, `"psy-chill"`, `"dark-psytrance"` para mejorar la clasificación por texto.

### Clasificación Geográfica
Los eventos se enriquecen con:
- **Continente**: América, Europa, Asia, África, Oceanía
- **Subcontinente**: Europa del Norte, Europa Central, etc.

### Loop de Mejora Continua
Un loop iterativo que prueba diferentes configuraciones de búsqueda (keywords de Facebook, visitas de enriquecimiento, ciudades de RA, horizonte de fechas) para encontrar la mejor combinación y superar los **300 eventos consolidados**.

## Archivos

### Principales
- `main.py`: Orquestador principal (usa submodulos)
- `scrapers/facebook_mcp.py`: Extracción de Facebook (SERP + grupos)
- `scrapers/resident_advisor.py`: Extracción de RA (GraphQL)
- `scrapers/goabase.py`: Extracción de Goabase (API JSON)
- `scrapers/songkick.py`: Extracción de Songkick (GraphQL)

### Utilitarios
- `utils/helpers.py`: Utilidades CSV y helpers
- `utils/geo_utils.py`: Clasificación de países por continente/subcontinente
- `utils/db_manager.py`: Gestor SQLite opcional para eventos
- `utils/event_extractor.py`: Extractor con clasificador de subgénero

### Configuración y Logs
- `config_grupos.json`: Configuración de países/ciudades/subgéneros para Facebook y RA
- `config_loop.json`: Configuración para el loop de mejora continua
- `mejor_configuracion.json`: Configuración persistida con mejores resultados
- `historial_loop.json`: Historial de ejecuciones del loop
- `reporte_loop.md`: Reporte markdown del loop

### Dependencias
Este proyecto requiere:
- Python 3.9+
- playwright (para Selenium)
- requests
- beautifulsoup4
- instaloader (opcional, para Instagram)

## Uso

### Ejecución Principal
```bash
python3 main.py
```

Esto generará `eventos_encontrados.csv` con todos los eventos consolidados, clasificación por subgénero y ubicación.

### Loop de Mejora Continua
```bash
python3 loop_mejora.py
```

Prueba automáticamente configuraciones para encontrar la mejor combinación (>300 eventos).

### Reiniciar Historial
```bash
python3 loop_mejora.py --reset
```

Elimina el historial y mejores configuraciones previas.

### Base de Datos SQLite Opcional
```bash
python3 -c "
from utils.db_manager import init_db, save_events, query_events, export_csv
init_db()  # Crea la base de datos
save_events(eventos_dict_list)  # Guarda eventos
results = query_events(subgenero='psytrance')  # Consulta
export_csv('eventos.db', 'eventos_salida.csv')  # Exporta
"
```

## Ejemplo de Salida CSV

```csv
nombre,fecha,lugar,pais,continente,subcontinente,fuente,organizador,email,link,subgenero
Pyramid Festival 2026 – SOVRA Edition,2026-08-03,Village Ilino,Serbia,Europa,Europa Central,Goabase,Pyramid Festival,info@pyramidfestival.com,https://www.goabase.net/festival/pyramid-festival-2026-sovra-edition/116250,psytrance
GOAROOM,2026-08-04,天神バッカス館 802F,Japan,Asia,Asia Oriental,Goabase,KINGBONGKONG,yuduru0327@gmail.com,https://www.goabase.net/party/goaroom/117988,goa
```

## Modo de Contribución

1. Haz un fork de este repositorio.
2. Crea una rama para tu feature (`git checkout -b feature/nombre-feature`).
3. Commit los cambios (`git commit -a`).
4. Push a la rama (`git push origin feature/nombre-feature`).
5. Crea un Pull Request.

## Licencia

Este proyecto está bajo licencia MIT. Ver `LICENSE` para más detalles.

## Advertencias

- **Todo local y gratuito**: No hay APIs externas ni servicios de pago.
- **Sin login**: No se requieren credenciales de usuario para ninguna fuente.
- **Tor inestable**: Chromium vía Tor puede fallar ocasionalmente, mitigado con fallback directo.
- **Email limitado**: FB no expone emails en páginas públicas; el email solo viene de caches internos y del organizador extraído de la página del evento cuando es posible.
- **Subgénero en lowercase**: El clasificador de subgénero devuelve texto en minúsculas; considera usar titlecase si lo prefieres.
- **CI**: CI está actualmente limitado por el tiempo de espera del proxy.

## Problemas Conocidos

1. **Email limitado**: FB no expone emails en páginas públicas. Los emails vienen del organizador extraído de la página del evento o del caché interno.
2. **Subgénero "general"**: Los eventos sin coincidencia clara de subgénero reciben "general". Considera usar un threshold de similitud más estricto si necesitas eventos solo-psytrance.
3. **CI limitada**: Los jobs de CI tienen tiempos de espera cortos para operaciones de scraping intensivas.
4. **Tor inestable**: Los intentos de Chromium vía Tor a Startpage fallan ocasionalmente; se usa directo como fallback.

## Mejoras Futuras (Backlog)

- [ ] **Mejor ranking SERP**: Usar éxito en SERP para prioridad de keywords.
- [ ] **Extracción de precio de tickets**: Parsear precios de entrada/entradas de FB y RA.
- [ ] **Resumen de lineup**: Extraer lista de DJ/artistas destacados de cada evento.
- [ ] **Validación de ubicación**: Confirmar eventos en la ciudad/país declarado.
- [ ] **Deduplicación más robusta**: Reducir aún más duplicados cruzados.
- [ ] **Iteración de construcción de modelos**: Entrenar clasificador de subgénero supervisado.
- [ ] **Filtrado basado en tráfico**: Aplicar límite de visitas por keyword para evitar sobrecarga.
- [ ] **Cluster por género**: Agrupar eventos similares para insights de tendencias.

## Referencias

- [Resident Advisor API](https://ra.co/api)
- [Goabase API](https://www.goabase.net/api)
- [Songkick API](https://www.songkick.com/api)
- [Facebook GraphQL](https://developers.facebook.com/docs/graph-api)
- [Playwright](https://playwright.dev/)
- [instaloader](https://instaloader.github.io/)

## Contacto

Para preguntas o contribuciones, contacta al maintainer en el repositorio GitHub.