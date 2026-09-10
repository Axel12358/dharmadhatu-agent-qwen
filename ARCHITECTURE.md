# Arquitectura de Scraping Auto-Mejora Continua (Self-Improving Scraper Architecture)

## Visión General

Sistema de scraping que **aprende de cada ejecución**, generaliza patrones semánticamente, se auto-cura ante cambios de sitio, y optimiza sus parámetros automáticamente. Basado en arquitecturas probadas: WebScout, AutoScraper, Kadoa, agentes de DeepCura.

---

## Principios de Diseño

| Principio | Implementación |
|-----------|----------------|
| **Deterministic Outcomes, Non-Deterministic Paths** | Múltiples estrategias (requests, Playwright, SERP, LLM), fallback ordenado por éxito histórico |
| **Every Execution Teaches** | Cada run escribe: éxitos, fallos, recuperaciones → vector store + métricas |
| **Semantic Pattern Reuse** | Embeddings hash locales → KNN search para generalizar across páginas similares |
| **Contract-First Extraction** | Pydantic schemas = data contracts → validación estricta antes de publicar |
| **Adaptive Recovery** | 4 estrategias ordenadas por éxito por dominio: LLM-agent → Act → Extract-refined → Vision |
| **Observability via Traces** | Finite state machine por record: queued→fetched→parsed→validated→corrected→published/quarantined |

---

## Capas de la Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                    ORQUESTADOR (ImprovementLoop)                │
│  - Coordina agentes  •  Persistencia vectorial  •  Métricas    │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   INGEST AGENT   │ │   PARSE AGENT    │ │   QA AGENT       │
│  (anti_block +   │ │  (EventExtractor │ │  (Pydantic       │
│   Tor + proxy)   │ │   + LLM local)   │ │   validation)    │
└──────────────────┘ └──────────────────┘ └──────────────────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               ▼
                    ┌──────────────────────┐
                    │   RETRY AGENT        │
                    │  (policy-driven      │
                    │   recovery strategies│
                    │   ordered by fitness)│
                    └──────────────────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │   INTEGRATOR AGENT   │
                    │  (dedup semántico    │
                    │   + idempotent write)│
                    └──────────────────────┘
```

---

## Estado del Record (Finite State Machine)

```
QUEUED → FETCHED → PARSED → VALIDATED → [CORRECTED] → PUBLISHED
                    ↓           ↓
               QUARANTINED  RETRY_QUEUE
```

Cada transición emite **trace estructurado** (no solo logs): agent, duration, inputs, confidence, reason.

---

## Vector Store Local (Pattern Learning)

**Embeddings:** hash local determinístico (768-dim) — sin API keys, sin dependencias externas.

**Índice:** HNSW en archivo local (FAISS o ChromaDB embebido) — persistencia JSONL.

**Patrón guardado:**
```json
{
  "id": "uuid",
  "url_pattern": "instagram.com/explore/tags/*",
  "target": "psytrance event extraction",
  "strategy": "playwright_post",
  "selector_logic": "meta[property='og:description'] + DOM fallback",
  "embedding": [0.12, -0.45, ...],  // 768-dim
  "fitness": 0.87,                  // Wilson score + time decay
  "success_count": 23,
  "failure_count": 2,
  "last_succeeded_at": "2026-07-31T...",
  "created_at": "2026-07-15T...",
  "domain": "instagram.com"
}
```

**Búsqueda:** KNN cosine similarity → re-rank por `composite = similarity × 0.6 + fitness × 0.4`.

**Auto-poda:** fitness < 0.05 Y failures ≥ 3 → archivado.

---

## Estrategias de Recuperación (Ordered by Domain Fitness)

| Orden | Estrategia | Qué hace | Cuándo usar |
|-------|------------|----------|-------------|
| 1 | **LLM Agent** | LLM asiste en generar selector/XPath nuevo | Cambio estructural mayor, SPA compleja |
| 2 | **Act/Interact** | Dismissa modales, cookies, scroll, click "ver más" → re-extract | Bloqueos UI (GDPR, paywalls, overlays) |
| 3 | **Extract Refined** | Re-extraction con instrucciones enriquecidas (target main content) | Selectores muy amplios, ruido lateral |
| 4 | **Vision/LLM Multimodal** | Screenshot + prompt visual → localiza elementos por apariencia | Canvas, ofuscación extrema, sin DOM accesible |

El orden se **recalcula por dominio** tras cada ejecución usando `strategy_stats:{domain}:{strategy}`.

---

## Confianza y Validación (Contract-First)

**Schema Pydantic por fuente:**
```python
class InstagramEvent(BaseModel):
    nombre: str = Field(..., min_length=3)
    fecha: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$|^N/A$")
    lugar: str
    ciudad: str
    pais: str = Field(default="España")
    subgenero: str = Field(pattern="^(darkpsy|forest|hitech|progressive|fullon|goa|suomisaundi|zenonesque|psycore|psybient|)$")
    organizador: str
    url: HttpUrl
    descripcion: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    source_strategy: str
```

**Validación en capas:**
1. **Schema validation** — Pydantic (tipos, formatos, enums)
2. **Cross-check determinista** — Compara contra JSON-LD / microdata de la página
3. **Grounding LLM** — LLM verifica que valores extraídos existan en el texto original
4. **Identity/locale check** — Ciudad/pais coherentes, subgénero válido
5. **Confidence scoring** — 1.0 si match exacto JSON-LD, sino acumula bonuses por checks pasados

**Solo publica si `confidence ≥ 0.7` Y `reliable=True`** (todos los required fields verificados).

---

## Loop de Mejora Continua (Cada Ejecución)

```
┌────────────────────────────────────────────────────────────────┐
│ 1. PLAN: Cargar patrones vectoriales + métricas históricas     │
│    → Ordenar estrategias por fitness por dominio               │
│    → Ajustar params: timeouts, max_posts, priority             │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ 2. EXECUTE: Por cada hashtag/grupo/consulta                    │
│    a. Vector search → cached pattern? (composite > threshold)  │
│    b. Sí → Ejecutar pattern → VALIDATE → si ok: PUBLISH        │
│    c. No / Fallo → FRESH EXTRACT (strategy ordenada)           │
│    d. Fallo → RECOVERY (4 estrategias adaptativas)             │
│    e. Cada paso escribe trace + actualiza vector store         │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ 3. LEARN: Post-ejecución                                       │
│    - Patrones exitosos → store embedding + fitness update      │
│    - Patrones fallidos → increment failure, raise threshold    │
│    - Recuperaciones exitosas → store recovery pattern          │
│    - Métricas agregadas → loop_optimizer.guardar_iteracion()   │
│    - Auto-poda patterns staleness                              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│ 4. OPTIMIZE: Ajuste de parámetros globales                     │
│    - loop_optimizer.ajustar_parametros(config_base)            │
│    - Sugerencias: MAX_POSTS, TIMEOUT, priorizar_strategy       │
│    - Persistir mejor_config.json                               │
└────────────────────────────────────────────────────────────────┘
```

---

## Métricas Clave (Service-Level)

| Métrica | Objetivo | Acción si degrada |
|---------|----------|-------------------|
| **Extraction Success Rate** | > 80% | Aumentar recovery attempts, bajar threshold |
| **Mean Time to Repair** | < 5 min | Optimizar recovery order, cachear patterns |
| **% Rows Auto-Corrected** | > 60% | Mejorar LLM agent prompts, añadir vision |
| **% Rows Human Review** | < 5% | Subir confidence threshold, más validación |
| **Pattern Fitness Avg** | > 0.6 | Podar agresivamente, generar patterns frescos |
| **Vector Store Hit Rate** | > 40% | Más ejecuciones = más patterns, embedding quality |

---

## Integración con Módulos Existentes

| Módulo | Punto de Integración | Cambio |
|--------|---------------------|--------|
| `instagram_scraper.py` | `scrape_instagram_events()` → wrapper `ImprovementLoop.run()` | Aditivo |
| `facebook_events_from_groups.py` | Fase 2 fallback → `ImprovementLoop.run()` | Aditivo |
| `facebook_public_events.py` | `_buscar()` → `ImprovementLoop.run()` | Aditivo |
| `event_extractor.py` | `EventExtractor.extract_all()` → `ParseAgent.extract()` | Wrapper |
| `anti_block.py` | `create_stealth_context()` → `IngestAgent.fetch()` | Ya listo (Tor) |
| `loop_optimizer.py` | Ya existe → `ImprovementLoop` lo usa internamente | Extendido |

---

## Stack Tecnológico Local (0 Dependencias Cloud)

| Componente | Tecnología | Instalación |
|------------|------------|-------------|
| **LLM Extraction/Recovery** | LLM hibrido via `core/hibrido.py` (opencode/freellmpool) + fallback regex | `pip install -r requirements.txt` |
| **Embeddings** | Hash local determinístico (sin API) | Ninguna |
| **Vector Index** | FAISS (CPU) o ChromaDB embebido | `pip install faiss-cpu chromadb` |
| **Browser** | Playwright (Chromium) | `playwright install chromium` |
| **Proxy/Anonymity** | Tor (proceso hijo gestionado) | `brew install tor` / `apt install tor` |
| **Schemas/Validation** | Pydantic v2 | `pip install pydantic` |
| **Orchestration** | `loop_optimizer.py` + `core/orquestador.py` | Código propio |

---

## Archivos a Crear/Modificar

```
scrapers/
├── vector_store.py            # NUEVO: FAISS/Chroma local + embeddings hash
├── agents/
│   ├── __init__.py
│   ├── ingest_agent.py        # NUEVO: Fetch con anti_block + Tor
│   ├── parse_agent.py         # NUEVO: Extracción regex + fallback
│   ├── qa_agent.py            # NUEVO: Validación Pydantic + confidence
│   ├── retry_agent.py         # NUEVO: Recovery strategies adaptativas
│   └── integrator_agent.py    # NUEVO: Dedup semántico + write idempotente
├── schemas/
│   ├── __init__.py
│   └── event_schemas.py       # NUEVO: Pydantic models por fuente
├── anti_block.py              # YA EXISTE: Tor + proxies + stealth
├── loop_optimizer.py          # YA EXISTE: Multi-strategy scoring
├── instagram_scraper.py       # MODIFICAR: Integrar ImprovementLoop
├── facebook_events_from_groups.py  # MODIFICAR: Integrar ImprovementLoop
└── facebook_public_events.py       # MODIFICAR: Integrar ImprovementLoop
```

---

## Próximos Pasos de Implementación

1. **Crear `schemas/event_schemas.py`** — Contratos Pydantic
2. **Crear `vector_store.py`** — FAISS local + embeddings nomic-embed-text
3. **Crear `agents/*.py`** — 5 agentes especializados
4. **Crear `improvement_loop.py`** — Orquestador + FSM + traces
5. **Integrar en scrapers existentes** — Wrapper aditivo en `scrape_*_events()`
6. **Test end-to-end** — Verificar loop: exec → learn → optimize → exec mejorado