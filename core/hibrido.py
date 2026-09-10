# -*- coding: utf-8 -*-
"""
Clasificador Híbrido — Modelos del asistente (opencode) + cloud free (freellmpool)
con fallback a regex. Evita bloqueo por uso frecuente rotando proveedores.

Estrategia:
  Tier 1: Modelos opencode/* gratuitos (assistant) — 6 modelos sin API key
  Tier 2: Modelos freellmpool/* gratuitos (cloud) — Groq/Mistral/etc vía pool
  Tier 3: Regex fallback (event_extractor.py) — siempre funciona, sin LLM

Cada tier rota proveedores; si uno falla (429/timeout) se marca bloqueado 5min
y se prueba el siguiente. Así el bot nunca se bloquea aunque un proveedor
se sature por uso frecuente.

Uso:
  from core.hibrido import HybridLLMClient
  client = HybridLLMClient()
  resp = client.generate("clasifica: ...", system="solo JSON")

  # Para reclasificación batch:
  from core.hibrido import reclasificar_no_psy_hibrido
  reclasificar_no_psy_hibrido(events)

Requiere: opencode CLI en PATH (ya instalado). No requiere API keys.
"""
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BLOQUEO_FILE = _PROJECT_ROOT / "hibrido_bloqueos.json"
_BLOQUEO_TTL = 300  # 5 min

# Tier 1: modelos opencode gratuitos (assistant) — sin API key
MODELOS_ASSISTANT = [
    "opencode/muse-spark-1.2-contributor-free",
    "opencode/big-pickle",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/nemotron-3-ultra-free",
]

# Tier 2: cloud free vía freellmpool — rotación si assistant se satura
MODELOS_CLOUD = [
    "freellmpool/groq/openai/gpt-oss-20b",
    "freellmpool/mistral/mistral-small-latest",
    "freellmpool/llm7/fast",
    "freellmpool/auto",
]

# Todos los modelos en orden de intento (assistant primero, cloud fallback)
TODOS_MODELOS = MODELOS_ASSISTANT + MODELOS_CLOUD

TIMEOUT_OPCODE = 30  # por llamada


def _cargar_bloqueos() -> Dict[str, float]:
    if _BLOQUEO_FILE.exists():
        try:
            data = json.loads(_BLOQUEO_FILE.read_text())
            # limpiar expirados
            ahora = time.time()
            return {k: v for k, v in data.items() if ahora - v < _BLOQUEO_TTL}
        except Exception:
            pass
    return {}


def _guardar_bloqueos(bloqueos: Dict[str, float]):
    try:
        _BLOQUEO_FILE.write_text(json.dumps(bloqueos, indent=2))
    except Exception:
        pass


def _esta_bloqueado(modelo: str, bloqueos: Dict[str, float]) -> bool:
    ts = bloqueos.get(modelo, 0)
    return (time.time() - ts) < _BLOQUEO_TTL


def _marcar_bloqueado(modelo: str):
    bloqueos = _cargar_bloqueos()
    bloqueos[modelo] = time.time()
    _guardar_bloqueos(bloqueos)


def _llamar_opencode(modelo: str, prompt: str, system: str = "") -> Optional[str]:
    """Llama a opencode run con el modelo dado. Retorna texto o None si falla."""
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    # Escapar para shell — usamos lista, no shell
    cmd = [
        "opencode", "run", full_prompt,
        "-m", modelo,
        "--format", "json",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_OPCODE,
            cwd=str(_PROJECT_ROOT),
        )
        if result.returncode != 0:
            # Detectar 429 / rate limit en stderr
            err = (result.stderr or "") + (result.stdout or "")
            if "429" in err or "rate" in err.lower() or "quota" in err.lower():
                _marcar_bloqueado(modelo)
                logger.debug(f"Rate limited: {modelo}")
            return None
        # Parsear JSON lines, buscar type=text
        for line in result.stdout.splitlines():
            try:
                obj = json.loads(line)
                if obj.get("type") == "text":
                    text = obj.get("part", {}).get("text", "")
                    if text:
                        return text.strip()
            except Exception:
                continue
        return None
    except subprocess.TimeoutExpired:
        _marcar_bloqueado(modelo)
        logger.debug(f"Timeout: {modelo}")
        return None
    except Exception as e:
        logger.debug(f"opencode call failed {modelo}: {e}")
        return None


class HybridLLMClient:
    """Cliente híbrido con rotación y fallback anti-bloqueo."""

    def __init__(self):
        self.bloqueos = _cargar_bloqueos()

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Optional[str]:
        # Tier 1+2: rotar modelos opencode/freellmpool evitando bloqueados
        self.bloqueos = _cargar_bloqueos()
        for modelo in TODOS_MODELOS:
            if _esta_bloqueado(modelo, self.bloqueos):
                continue
            resp = _llamar_opencode(modelo, prompt, system)
            if resp:
                return resp
            # refrescar bloqueos por si se marcó
            self.bloqueos = _cargar_bloqueos()

        return None

    def is_available(self) -> bool:
        # Probar un modelo ligero rápido
        self.bloqueos = _cargar_bloqueos()
        for modelo in MODELOS_ASSISTANT[:2]:
            if not _esta_bloqueado(modelo, self.bloqueos):
                return True
        return False


# Singleton
_hybrid_instance = None

def get_hybrid_client() -> HybridLLMClient:
    global _hybrid_instance
    if _hybrid_instance is None:
        _hybrid_instance = HybridLLMClient()
    return _hybrid_instance


# ---------------------------------------------------------------------------
# Helpers de alto nivel con fallback regex
# ---------------------------------------------------------------------------
def _fallback_regex_clasificar(nombre: str) -> str:
    """Fallback sin LLM: usa event_extractor si existe, sino heurística simple."""
    try:
        from scrapers.event_extractor import EventExtractor
        sg = EventExtractor.clasificar_subgenero(nombre)
        if sg and sg not in ("", "general", "no_psy"):
            return sg
    except Exception:
        pass
    # Heurística mínima
    n = (nombre or "").lower()
    if any(k in n for k in ["dark", "night", "twilight", "psycore"]):
        return "darkpsy"
    if any(k in n for k in ["forest", "organic", "woods"]):
        return "forest"
    if any(k in n for k in ["progressive", "prog"]):
        return "progressive"
    if any(k in n for k in ["goa", "classic"]):
        return "goa"
    if any(k in n for k in ["chill", "ambient", "downtempo"]):
        return "psychill"
    if "psytrance" in n or "psy trance" in n:
        return "psytrance"
    return "NO_PSY"


def reclasificar_no_psy_hibrido(events: List[Dict], batch_size: int = 3) -> List[Dict]:
    """
    Reclasifica no_psy usando LLM híbrido (opencode + freellmpool) con fallback regex.
    Nunca se bloquea: si todos los LLMs fallan, usa regex.
    """
    no_psy = [e for e in events if e.get("subgenero", "").strip() == "no_psy"]
    if not no_psy:
        print("  ✅ Sin eventos no_psy para reclasificar")
        return events

    client = get_hybrid_client()
    SG_VALIDOS = {"psytrance", "darkpsy", "forest", "hitech", "progressive",
                  "fullon", "goa", "psychill", "psybient", "suomisaundi",
                  "twilight", "psychedelic"}

    reclasificados = 0
    por_llm = 0
    por_regex = 0

    for i in range(0, len(no_psy), batch_size):
        batch = no_psy[i:i + batch_size]
        lineas = [f"{j}: {e.get('nombre','')[:80]} @ {e.get('lugar','')[:40]}" for j, e in enumerate(batch)]
        batch_prompt = (
            f"Eventos musicales. Clasifica cada uno:\n"
            f"{chr(10).join(lineas)}\n"
            f"Subgeneros: psytrance,darkpsy,forest,hitech,progressive,"
            f"fullon,goa,psychill,psybient,suomisaundi,twilight,psychedelic,NO_PSY\n"
            f"Responde SOLO JSON array: [\"sg0\",\"sg1\",...]"
        )
        resp = client.generate(batch_prompt, system="Clasificador. Solo JSON array de strings.", temperature=0.05)

        if resp:
            try:
                clean = resp.strip()
                if "```" in clean:
                    clean = clean.replace("```json", "").replace("```", "").strip()
                m = re.search(r'\[.*\]', clean, re.DOTALL)
                if m:
                    clean = m.group(0)
                clasifs = json.loads(clean)
                if isinstance(clasifs, list):
                    for j, ev in enumerate(batch):
                        if j < len(clasifs):
                            sg = str(clasifs[j]).strip().lower()
                            if sg and sg != "no_psy" and sg in SG_VALIDOS:
                                ev["subgenero"] = sg
                                reclasificados += 1
                                por_llm += 1
                    continue
            except Exception:
                pass

        # Fallback regex por evento si LLM falló
        for ev in batch:
            sg = _fallback_regex_clasificar(ev.get("nombre", "") + " " + ev.get("organizador", ""))
            if sg != "NO_PSY" and sg in SG_VALIDOS:
                ev["subgenero"] = sg
                reclasificados += 1
                por_regex += 1

    print(f"  📊 Reclasificados: {reclasificados}/{len(no_psy)} (LLM: {por_llm}, regex: {por_regex})")
    return events
