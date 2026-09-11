# AGENTS.md — Estado de trabajo (handoff)

> Documento de continuidad: cualquier modelo que retome el trabajo DEBE leer esto
> antes de tocar nada. Describe protocolo, estado actual, cambios pendientes sin
> commitear y siguientes pasos. Última actualización: Sep 11 2026.

## PROTOCOLO (obligatorio, no negociable)

- **Siempre sumar, nunca restar** (aditivo, no destructivo).
- **Todo local, gratuito, sin login** (nada de APIs de pago, nada de credenciales).
- **Cualquier escritura al CSV `eventos_encontrados.csv` DEBE ir bajo
  `core/csv_lock.csv_locked_rows(path)`** (fcntl cooperativo). Cualquier `open("w")`
  directo rompe el dedup concurrente y trunca el CSV. No usar escritores directos.
- Los JSON de estado runtime (root) y `config/` están en `.gitignore` (no públicos).
- Commitear/pushear solo con control explícito de qué se staggea: des-stagear siempr
  los `*.json` de estado runtime antes de `git commit`. Los `*.md` en raíz se fuerzan
  con `git add -f` si es necesario.
- Solo commitear cuando el usuario lo pida / confirme el flujo (workflow activo).
- Al terminar cada tarea: `python3 -m py_compile <archivos>` para verificar sintaxis.

## OBJETIVO GENERAL

Bot que consolida un CSV limpio de eventos psytrance reales desde múltiples fuentes.
El CSV crece de forma orgánica (el objetivo "5000" se alcanza con descubrimiento
continuo, NO con los backups, que son mayormente duplicados).

## ESTADO ACTUAL (CSV)

`eventos_encontrados.csv`:
- ~2,856 filas únicas (por nombre+fecha); 2,872 líneas incl. header/multilínea.
- Organizador: ~95% (2,721). Email: ~59% (1,708). Subgénero: 100%.
- Los backups (`bak_jeandupont` 5,700 / `bak_filtro_falsos` 5,272) son >90% duplicados:
  `merge_5000.py` merge 13,287 brutos → 2,586 únicos reales.
- 80 links duplicados (variantes mínimas; el dedup semántico los usa, no borrar).

## SISTEMA DE ESCRITURA CSV (lock cooperativo)

Todos los escritores usan `csv_locked_rows`:
`core/orquestador.py`, `core/agente_coordinador.py`, `core/algebra_lineal.py`,
`core/matriz_busqueda.py`, `core/goabase_2027.py`, `core/fuentes_extra.py`,
`core/rellenar_na.py`, `core/mcp_clasificar.py`, `loop_completar.py`,
`venue_enricher.py`, `scrapers/instagram_dorks.py`, `merge_5000.py`.

## SCRAPERS ACTIVOS (plugin_loader)

Auto-descubrimiento en `scrapers/*.py` con `config_modulos.json`:
- **29 ON / 30**: agente_coordinador, dorks_resultados, edmdancedirectory,
  ektoplazm, enriquecer_fb_og, facebook_dorks, facebook_groups_sync,
  facebook_mejorado, fb_playwright_tor, goabase, instagram, instagram_dorks,
  instagram_fusion, instagram_public, instagram_scraper, isratrance,
  mcp_organizador, meetup_psy, psychill_space, psymedia, psynews, psytrance_pl,
  psytrancefestivals_tv, ra_promoter_dorks, reddit_psy, resident_advisor,
  setline, songkick, telegram.
- **OFF**: facebook_mcp (requiere login externo).

## FAQ FUENTES / DORKS

- `core/fuentes_extra.py`: 20 dorks (fase 4) — fue ampliado de 8 → 20 con
  facebook events, isratrance, ektoplazm, psymedia, años 2027-2028. `_merge` con lock.
- `core/goabase_2027.py`: goabase JSON/JSON-LD (fuente de oro) — años [2028,2027,2026],
  eventtypes indoor/club, 15 búsquedas por subgénero + `?status=new/update`, CAMPOS 13
  columnas, `escanear()` merge directo al CSV principal bajo lock.
- `core/matriz_busqueda.py`: combo subgénero×ciudad×año `site:facebook.com/events`.
  39 sinónimos × 130 ciudades (incl. 9 latin USA añadidas Sep 11). Consolida al final
  bajo lock. CLI: `--presupuesto N --ejecutar`.
- `scrapers/instagram_dorks.py`: dorks IG via DDG+Mojeek+Google stealth, función
  pública `scrape_instagram_dorks()` (200s presupuesto). Estado en
  `scrapers/instagram_dorks/*.json` (gitignored).
- `core/plugin_loader.py`: leer el `.py` COMPLETO (antes 8KB → `ra_promoter_dorks`
  nunca se detectaba). Ahora descubre 30.

## PROCESOS ACTIVOS (Sep 11 12:52, cluster del usuario)

- `main.py` (bot principal, ciclo orquestador) — corriendo.
- `loop_completar.py` — mina organizadores/emails de FB/IG/RA bajo lock.
- `matriz_busqueda --presupuesto 400 --ejecutar` — corriendo en /tmp/matriz_run.log.
- Tor pool: 3 identidades (9050/9051, 9052/9053, 9054/9055).

## COMMITS RECIENTES (feature/mcp-refactor)

- `e44d0c7` fix: matrix_search escribe CSV bajo csv_lock (concurrent-safe)
- `acae7ea` feat: scrape_instagram_dorks pública + fix nested func, google stealth
  import, Path state
- `f69a2e5` feat: ampliar fuentes (fuentes_extra 20 dorks, goabase 2027-28, plugin
  full read) + merge_5000.py + prompts/claude_instagram_dorks.md
- `eb445cc` fix: import Path en serp_worker.py
- `0491134` fix: algebra_lineal escribe CSV bajo csv_lock
- `0933038` chore: limpieza qwen/ollama + rename llm_hibrido→hibrido + CSV locking

## PENDIENTES

1. Recargar el MCP `dharmadhatu-clasificador` en OpenCode (renombramos el módulo qwen).
2. Decidir rename del repo GitHub `dharmadhatu-agent-qwen` → `dharmadhatu-agent`
   (`gh repo rename ...` + `git remote set-url origin ...`).
3. Cuando la matriz termine: verificar CSV creció (consolidación final bajo lock).
4. `enriquecer_contactos_dorks` dio 0 emails bajo saturación Tor; relanzar con
   timeout mayor cuando la matriz termine.
5. El arquétipo de parche IG de Claude (`/Users/angelgarcia/Downloads/instagram_dorks_patch.py`)
   usaba firmas INEXISTENTES — NO aplicar tal cual. La versión adaptada está en
   `instagram_dorks_patch.py` (raíz) y ya integrada en `scrapers/instagram_dorks.py`.