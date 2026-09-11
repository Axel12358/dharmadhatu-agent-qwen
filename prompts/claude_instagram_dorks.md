# Prompt: Completar `scrapers/instagram_dorks.py` — falta función pública

## Contexto del proyecto

Dharmadhatu Bot: recolecta eventos de psytrance hacia `eventos_encontrados.csv` (CSV con columnas `nombre,fecha,lugar,pais,continente,subcontinente,fuente,organizador,email,link,subgenero,tipo_lugar,contactos`).

El arranque usa `core/plugin_loader.py:descubrir_scrapers()` que **solo descubre funciones públicas** `def scrape_*(...)` en archivos de `scrapers/`. Todos los módulos deben ser **aditivos** (agregar, nunca borrar) y usar el **lock cooperativo** `core/csv_lock.py:csv_locked_rows` para escribir el CSV (fcntl), porque varios procesos (main.py, loop_completar, orquestador) escriben en paralelo. Sin lock = pisan el CSV.

## Problema

`scrapers/instagram_dorks.py` (1,807 líneas) está **incompleto**:

1. **No tiene ninguna función pública** — el `plugin_loader` NO lo detecta como scraper (por eso aparecía "OFF" en config_modulos.json aunque se ponga `"instagram_dorks": true`). Todas las funciones son privadas (`_`).
2. **Bug estructural**: `_buscar_google_con_reintentos_ig` (línea 1748, indentada 4 espacios) está **anidada dentro de** `_parse_google_results_ig` (que ya termina en línea 1745 con `return resultados`). Nunca se puede llamar desde el exterior, y todo su cuerpo quedó encajonado de forma incorrecta.
3. El archivo termina abruptamente (línea 1807) sin `if __name__ == "__main__"` ni orquestación.

## Estructura actual del archivo (AST real)

```
_verificar_tor_ig @297        # checa Tor
_es_relevante_ig @445         # filtra si un texto es evento psytrance
_get_extractor @469           # EventExtractor (scrapers/event_extractor.py)
_get_anti @491                # anti-block
_leer_json @513 / _escribir_json @533
_rotar_identidad_tor @569
_es_bloqueo_ddg @645 / _es_bloqueo_mojeek @663
_get_sesion @685
_random_sleep @727
_ejecutar_fuera_del_loop @747 (+ _wrapper @755)  # timeout por wall-clock
_es_post_instagram @803 / _es_perfil_instagram @809 / _es_organizador_instagram @823
_limpiar_titulo_post_ig @843
_extraer_autor_de_post @909
_cargar_config @997
_generar_dorks @1019          # combina subgéneros × ciudades × hashtags (10 dorks/run)
_buscar_ddg @1181
_buscar_mojeek @1333
_buscar_bing @1463
_google_search_playwright_ig @1609   # requiere STEALTH_AVAILABLE (falta? retorna None si no)
_parse_google_results_ig @1645
    _buscar_google_con_reintentos_ig @1748   # BUG: anidada aquí
```

Docstring del módulo dice la intención:
- Generar dorks naturales (no `site:`) subgénero × ciudad × hashtag
- Buscar en DDG → Mojeek (timeout 8s/motor); si falla, siguiente dork
- Por URL encontrada: si es post/reel → visitar y extraer caption+fecha+ubicación vía `_extraer_autor_de_post`; si es perfil → organizador (no evento)
- Pasar caption por EventExtractor, filtrar ruido, dedup local por URL, archivos de estado en `scrapers/instagram_dorks/`
- **Presupuesto 180s, rotación Tor**, semáforo global (máx 1 petición Tor), aditivo, nunca rompe el bot.

(Nota: partes como `_extraer_autor_de_post`, `_leer_json/_escribir_json` y referencias a `STEALTH_AVAILABLE`, `google_search_prioritario`, `scrape_facebook_dorks._buscar_dork_multimotor_tor` requieren verificar qué existe realmente — si hay helpers que ya no están, registrar como pendiente, no inventar.)

## Tarea

1. **Sacar `_buscar_google_con_reintentos_ig` de su anidamiento** y dejarla como función de nivel módulo (des-indentar correctamente).
2. **Añadir la función pública** `scrape_instagram_dorks(...) -> List[Dict]` con:
   - Firma compatible con `plugin_loader` (kwargs opcionales tipo `timeout`, rotación de Tor, presupuesto de tiempo).
   - Orquesta: `_generar_dorks()` → por dork: DDG → Mojeek → (Google si `STEALTH_AVAILABLE`) → parsear → filtrar relevancia con `_es_relevante_ig` → extraer evento por caption (si post) o registrar organizador (si perfil).
   - Presupuesto de reloj 180s (usar `_ejecutar_fuera_del_loop` para no colgar el proceso).
   - Saca eventos al CSVs **con lock**: `with csv_locked_rows("eventos_encontrados.csv") as (estado, _fn): ...   # aditivo por link, nunca borra`.
   - Dedup local por URL, archivos de estado LEÍDOS con `_leer_json/_escribir_json`.
   - Respeta el semáforo global de Tor (máx 1 petición).
   - Retorna la lista de eventos nuevos (Dict con las 13 columnas del CSV).
   - `if __name__ == "__main__": print(...)` al final para correrlo directo.
3. **No romper** lo que ya funciona; mantener el estilo (docstrings, prints con emoji, tolerancia a fallos: cualquier scraper que lance excepción se captura y no rompe el orquestador).

## Criterios de éxito

- `python3 -m py_compile scrapers/instagram_dorks.py` compila.
- `from core.plugin_loader import descubrir_scrapers; "instagram_dorks" in descubrir_scrapers()` = True.
- Al correr `scrape_instagram_dorks()` durante ≤4 min: termina, no cuelga, agrega (o no) eventos pero **nunca** reduce líneas de `eventos_encontrados.csv`, y no hace peticiones desde la IP real (siempre Tor/proxy).

## Archivos de referencia a leer primero

- `scrapers/instagram_dorks.py` (el que hay que arreglar)
- `scrapers/facebook_dorks.py` (mismo patrón de dorks — usa `_buscar_dork_multimotor_tor`)
- `scrapers/dorks_resultados.py` (mismo patrón con archivos de estado y `scrape_dorks_resultados`)
- `core/csv_lock.py` (el lock — el CSV igual con `extraesaction="ignore"`)
- `core/plugin_loader.py` (cómo se descubre el scraper)
- `scrapers/event_extractor.py` (SINONIMOS, EventExtractor)