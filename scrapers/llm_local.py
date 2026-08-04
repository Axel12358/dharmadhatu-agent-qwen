#!/usr/bin/env python3
"""
Cliente Ollama local (Qwen Coder) para tareas de extracción y clasificación de eventos.
Fallback a regex si Ollama no disponible. Uso aditivo en todo el pipeline.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
TIMEOUT_SECONDS = 30

_SUBGENEROS_VALIDOS = [
    "darkpsy", "forest", "hitech", "progressive", "fullon",
    "goa", "suomisaundi", "zenonesque", "psycore", "psybient"
]

_PROMPT_EXTRACT = """Eres un extractor de eventos de música electrónica (especialmente psytrance).
Analiza el texto y devuelve SOLO un array JSON válido con eventos encontrados.
Cada evento: {{\"nombre\": \"\", \"fecha\": \"YYYY-MM-DD\", \"lugar\": \"\", \"ciudad\": \"\", \"pais\": \"\", \"subgenero\": \"\", \"organizador\": \"\", \"descripcion\": \"\"}}
Subgéneros válidos: darkpsy, forest, hitech, progressive, fullon, goa, suomisaundi, zenonesque, psycore, psybient.
Reglas:
- Si no hay fecha clara, usa \"N/A\"
- Si no hay lugar/ciudad, usa \"N/A\"
- Subgénero: inferir del texto (ej: \"darkpsy\", \"forest party\", \"goa trance\")
- Organizador: @handles, nombres de promotores, páginas
- Descripción: resumen 1-2 frases
- Si NO hay evento real (solo promo, meme, venta entradas sin fecha), devuelve []
Texto: {text}"""

_PROMPT_DEDUP = """Compara estos eventos y devuelve SOLO un array JSON con los índices de los duplicados (0-based).
Eventos: {events}
Duplicados = mismo nombre+fecha+lugar (variaciones de escritura). Ejemplo: [1, 3] significa que eventos 1 y 3 son duplicados del 0.
Devuelve solo el array JSON, nada más."""

_PROMPT_CLASSIFY_SPAM = """Clasifica si este texto de Instagram es un EVENTO REAL de psytrance o RUIDO (promo genérica, meme, venta sin fecha, \"próximamente\", etc).
Texto: {text}
Responde SOLO: \"EVENTO\" o \"RUIDO\"."""

_PROMPT_NORMALIZE_LOC = """Normaliza esta ubicación a \"Ciudad, País\" estándar.
Entrada: {loc}
Ejemplos: \"BCN\" -> \"Barcelona, España\", \"berlin\" -> \"Berlín, Alemania\", \"goa india\" -> \"Goa, India\", \"madrid spain\" -> \"Madrid, España\".
Responde SOLO con la ubicación normalizada."""


class OllamaClient:
    def __init__(self, host: str = OLLAMA_HOST, model: str = MODEL_NAME, timeout: int = TIMEOUT_SECONDS):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def is_available(self) -> bool:
        try:
            r = self._get_client().get(f"{self.host}/api/tags", timeout=5)
            return r.status_code == 200 and any(m["name"].startswith(self.model.split(":")[0]) for m in r.json().get("models", []))
        except Exception:
            return False

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Optional[str]:
        if not self.is_available():
            return None
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "temperature": temperature,
                "stream": False,
                "options": {"num_predict": 512, "top_p": 0.9}
            }
            r = self._get_client().post(f"{self.host}/api/generate", json=payload, timeout=self.timeout)
            if r.status_code == 200:
                return r.json().get("response", "").strip()
        except Exception as e:
            logger.debug(f"Ollama generate error: {e}")
        return None

    def close(self):
        if self._client:
            self._client.close()
            self._client = None


_client_instance = None


def get_client() -> OllamaClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = OllamaClient()
    return _client_instance


def extract_events_llm(text: str, url: str = "") -> List[Dict]:
    """Extrae eventos usando Qwen Coder local. Fallback: lista vacía."""
    if not text or len(text.strip()) < 30:
        return []
    client = get_client()
    if not client.is_available():
        return []
    prompt = _PROMPT_EXTRACT.format(text=text[:2000])
    resp = client.generate(prompt, system="Eres un extractor preciso. Solo JSON válido.")
    if not resp:
        return []
    try:
        data = json.loads(resp)
        if isinstance(data, list):
            for ev in data:
                ev.setdefault("url", url)
                ev.setdefault("fuente", "instagram_llm")
                sg = (ev.get("subgenero") or "").lower()
                if sg and sg not in _SUBGENEROS_VALIDOS:
                    ev["subgenero"] = ""
            return data
    except json.JSONDecodeError:
        pass
    return []


def dedup_semantic_llm(events: List[Dict]) -> List[Dict]:
    """Elimina duplicados semánticos usando LLM. Fallback: lista original."""
    if len(events) < 2:
        return events
    client = get_client()
    if not client.is_available():
        return events
    evs_str = json.dumps([{k: v for k, v in e.items() if k in ("nombre", "fecha", "lugar", "ciudad")} for e in events], ensure_ascii=False)
    prompt = _PROMPT_DEDUP.format(events=evs_str)
    resp = client.generate(prompt, system="Solo array JSON de índices duplicados.")
    if not resp:
        return events
    try:
        dup_indices = json.loads(resp)
        if isinstance(dup_indices, list):
            keep = [i for i in range(len(events)) if i not in dup_indices]
            return [events[i] for i in keep]
    except json.JSONDecodeError:
        pass
    return events


def classify_spam_llm(text: str) -> bool:
    """True si es RUIDO (spam/promo sin evento), False si es EVENTO real. Fallback: False."""
    if not text or len(text.strip()) < 20:
        return True
    client = get_client()
    if not client.is_available():
        return False
    prompt = _PROMPT_CLASSIFY_SPAM.format(text=text[:1500])
    resp = client.generate(prompt, system="Clasificador binario. Solo EVENTO o RUIDO.")
    if not resp:
        return False
    return "RUIDO" in resp.upper()


def normalize_location_llm(loc: str) -> str:
    """Normaliza ubicación a 'Ciudad, País'. Fallback: original."""
    if not loc or loc.strip().upper() in ("N/A", "UNKNOWN", ""):
        return "N/A"
    client = get_client()
    if not client.is_available():
        return loc
    prompt = _PROMPT_NORMALIZE_LOC.format(loc=loc)
    resp = client.generate(prompt, system="Normalizador de ubicaciones. Solo 'Ciudad, País'.")
    if resp and "," in resp and len(resp) < 80:
        return resp.strip()
    return loc


if __name__ == "__main__":
    c = get_client()
    print("Ollama disponible:", c.is_available())
    if c.is_available():
        test = "🌲 Forest Psytrance Festival 15-17 Agosto @ Bosque Mágico, Barcelona. Darkpsy, hitech, forest vibes. Org: @darkforestcrew"
        print("Extract:", extract_events_llm(test))
        print("Spam:", classify_spam_llm(test))
        print("Norm loc:", normalize_location_llm("BCN"))