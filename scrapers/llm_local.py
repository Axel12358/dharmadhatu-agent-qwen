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


# ---------------------------------------------------------------------------
# Batch processing: reclasificar no_psy + rellenar N/A
# ---------------------------------------------------------------------------
_PROMPT_RECLASIFICAR = """Eres un experto en música electrónica y psytrance.
Analiza este evento y clasifícalo en UN subgénero psytrance específico.

Evento: {nombre}
Fuente: {fuente}
Lugar: {lugar}
Organizador: {organizador}

Subgéneros VÁLIDOS (elige SOLO uno):
- psytrance (default si es psytrance genérico)
- darkpsy (oscuro, agresivo, tempos rápidos)
- forest (organic, nature sounds, deep)
- hitech (muy rápido, 150+ BPM)
- progressive (lento, melódico, 135-145 BPM)
- fullon (energético, melódico, mainstream psy)
- goa (clásico, espiritual, indio)
- psychill / psybient (relajado, downtempo)
- suomisaundi (finlandés, experimental)
- twilight (oscuro-melódico, 145-155 BPM)
- psychedelic (psicodélico general)

Si NO es un evento psytrance real (techno, house, drum&bass, hip-hop, etc), responde: NO_PSY

Responde SOLO el nombre del subgénero, nada más."""


_PROMPT_EMAIL = """Analiza este evento de música y extrae el email de contacto si aparece en el texto.
Evento: {nombre}
Organizador: {organizador}
Link: {link}
Responde SOLO el email (ej: info@foo.com) o N/A si no hay."""


_PROMPT_ORGANIZADOR = """Extrae el nombre del organizador/colectivo que presenta este evento.
Evento: {nombre}
Fuente: {fuente}
Responde SOLO el nombre del organizador, o N/A si no se puede determinar."""


def reclasificar_no_psy(events: List[Dict], batch_size: int = 3) -> List[Dict]:
    """
    Reclasifica eventos marcados como 'no_psy' usando LLM.
    Batch de 3 eventos por prompt para ser eficiente.
    Devuelve la lista actualizada (solo modifica subgenero='no_psy').
    Fallback híbrido (opencode/freellmpool + regex) si Ollama no está.
    """
    client = get_client()
    if not client.is_available():
        print("  ⚠️ Ollama no disponible — usando LLM híbrido (opencode/freellmpool + regex)")
        try:
            from core.llm_hibrido import reclasificar_no_psy_hibrido
            return reclasificar_no_psy_hibrido(events, batch_size=batch_size)
        except Exception as e:
            print(f"  ⚠️ Híbrido no disponible: {e}")
            return events

    no_psy = [e for e in events if e.get("subgenero", "").strip() == "no_psy"]
    if not no_psy:
        print("  ✅ Sin eventos no_psy para reclasificar")
        return events

    print(f"  🤖 Reclasificando {len(no_psy)} eventos no_psy con LLM...")
    reclasificados = 0
    errores = 0

    SG_VALIDOS = {"psytrance", "darkpsy", "forest", "hitech", "progressive",
                  "fullon", "goa", "psychill", "psybient", "suomisaundi",
                  "twilight", "psychedelic"}

    for i in range(0, len(no_psy), batch_size):
        batch = no_psy[i:i + batch_size]
        # Prompt corto y directo
        lineas = []
        for j, ev in enumerate(batch):
            nombre = ev.get("nombre", "")[:80]
            lugar = ev.get("lugar", "")[:40]
            lineas.append(f"{j}: {nombre} @ {lugar}")
        eventos_str = "\n".join(lineas)

        batch_prompt = (
            f"Eventos musicales. Clasifica cada uno:\n"
            f"{eventos_str}\n"
            f"Subgeneros: psytrance,darkpsy,forest,hitech,progressive,"
            f"fullon,goa,psychill,psybient,suomisaundi,twilight,psychedelic,NO_PSY\n"
            f"JSON array: [\"sg0\",\"sg1\",...]"
        )

        resp = client.generate(
            batch_prompt,
            system="Clasificador. Solo JSON array de strings.",
            temperature=0.05,
        )

        if not resp:
            errores += len(batch)
            continue

        try:
            resp_clean = resp.strip()
            # Limpiar code blocks markdown
            if "```" in resp_clean:
                resp_clean = resp_clean.replace("```json", "").replace("```", "")
                resp_clean = resp_clean.strip()
            # Buscar array JSON en la respuesta
            import re as _re
            arr_match = _re.search(r'\[.*\]', resp_clean, _re.DOTALL)
            if arr_match:
                resp_clean = arr_match.group(0)
            clasificaciones = json.loads(resp_clean)
            if isinstance(clasificaciones, list):
                for j, ev in enumerate(batch):
                    if j < len(clasificaciones):
                        sg = clasificaciones[j].strip().lower()
                        if sg and sg != "no_psy" and sg in SG_VALIDOS:
                            ev["subgenero"] = sg
                            reclasificados += 1
        except (json.JSONDecodeError, TypeError):
            errores += len(batch)

    print(f"  📊 Reclasificados: {reclasificados}/{len(no_psy)} "
          f"(errores: {errores})")
    return events


def rellenar_email_n_a(events: List[Dict], max_procesar: int = 50) -> List[Dict]:
    """
    Rellena email N/A analizando nombre+organizador con LLM.
    Procesa solo los primeros max_procesar (para no sobrecargar).
    Fallback híbrido si Ollama no está.
    """
    client = get_client()
    use_hybrid = False
    hybrid = None
    if not client.is_available():
        try:
            from core.llm_hibrido import get_hybrid_client
            hybrid = get_hybrid_client()
            use_hybrid = True
            print("  ⚠️ Ollama no disponible — usando híbrido para emails")
        except Exception:
            return events

    sin_email = [
        e for e in events
        if e.get("email", "").strip().lower() in ("n/a", "", "na")
    ]
    if not sin_email:
        return events

    procesar = sin_email[:max_procesar]
    print(f"  🤖 Buscando emails en {len(procesar)} eventos (de {len(sin_email)} sin email)...")
    encontrados = 0

    for ev in procesar:
        prompt = _PROMPT_EMAIL.format(
            nombre=ev.get("nombre", "")[:100],
            organizador=ev.get("organizador", "")[:60],
            link=ev.get("link", "")[:100],
        )
        if use_hybrid:
            resp = hybrid.generate(prompt, system="Extractor de emails. Solo email o N/A.", temperature=0.05)
        else:
            resp = client.generate(prompt, system="Extractor de emails. Solo email o N/A.", temperature=0.05)
        if resp and "@" in resp and "." in resp and "N/A" not in resp:
            email = resp.strip().strip('"').strip("'").lower()
            if re.match(r"^[\w.\-+]+@[\w.\-]+\.\w+$", email):
                ev["email"] = email
                encontrados += 1

    print(f"  📊 Emails encontrados: {encontrados}/{len(procesar)}")
    return events


def rellenar_organizador_n_a(events: List[Dict], max_procesar: int = 30) -> List[Dict]:
    """
    Rellena organizador N/A analizando nombre del evento con LLM.
    Fallback híbrido si Ollama no está.
    """
    client = get_client()
    use_hybrid = False
    hybrid = None
    if not client.is_available():
        try:
            from core.llm_hibrido import get_hybrid_client
            hybrid = get_hybrid_client()
            use_hybrid = True
            print("  ⚠️ Ollama no disponible — usando híbrido para organizadores")
        except Exception:
            return events

    sin_org = [
        e for e in events
        if e.get("organizador", "").strip().lower() in ("n/a", "", "na")
    ]
    if not sin_org:
        return events

    procesar = sin_org[:max_procesar]
    print(f"  🤖 Buscando organizadores en {len(procesar)} eventos...")
    encontrados = 0

    for ev in procesar:
        prompt = _PROMPT_ORGANIZADOR.format(
            nombre=ev.get("nombre", "")[:150],
            fuente=ev.get("fuente", ""),
        )
        if use_hybrid:
            resp = hybrid.generate(prompt, system="Extractor de organizadores. Solo nombre o N/A.", temperature=0.05)
        else:
            resp = client.generate(prompt, system="Extractor de organizadores. Solo nombre o N/A.", temperature=0.05)
        if resp and "N/A" not in resp and len(resp.strip()) >= 3:
            ev["organizador"] = resp.strip()[:100]
            encontrados += 1

    print(f"  📊 Organizadores encontrados: {encontrados}/{len(procesar)}")
    return events


def procesar_csv_llm(csv_path: str = "eventos_encontrados.csv") -> Dict[str, int]:
    """
    Pipeline completo: carga CSV, reclasifica no_psy, rellena N/A, guarda.
    Devuelve estadísticas.
    """
    import csv as _csv
    import os

    if not Path(csv_path).exists():
        print(f"  ❌ CSV no encontrado: {csv_path}")
        return {}

    # Cargar
    with open(csv_path, "r", encoding="utf-8") as f:
        events = [dict(r) for r in _csv.DictReader(f)]

    total_antes = len(events)
    no_psy_antes = sum(1 for e in events if e.get("subgenero", "").strip() == "no_psy")
    email_na_antes = sum(1 for e in events if e.get("email", "").strip().lower() in ("n/a", "", "na"))
    org_na_antes = sum(1 for e in events if e.get("organizador", "").strip().lower() in ("n/a", "", "na"))

    print(f"  📂 CSV: {total_antes} eventos")
    print(f"     no_psy: {no_psy_antes} | email N/A: {email_na_antes} | org N/A: {org_na_antes}")

    # Procesar
    events = reclasificar_no_psy(events)
    events = rellenar_email_n_a(events)
    events = rellenar_organizador_n_a(events)

    # Contar después
    no_psy_despues = sum(1 for e in events if e.get("subgenero", "").strip() == "no_psy")
    email_na_despues = sum(1 for e in events if e.get("email", "").strip().lower() in ("n/a", "", "na"))
    org_na_despues = sum(1 for e in events if e.get("organizador", "").strip().lower() in ("n/a", "", "na"))

    # Guardar
    keys = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar"]
    tmp = csv_path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for ev in events:
            writer.writerow(ev)
    os.replace(tmp, csv_path)

    stats = {
        "total": total_antes,
        "no_psy_antes": no_psy_antes,
        "no_psy_despues": no_psy_despues,
        "reclasificados": no_psy_antes - no_psy_despues,
        "email_antes": email_na_antes,
        "email_despues": email_na_despues,
        "email_encontrados": email_na_antes - email_na_despues,
        "org_antes": org_na_antes,
        "org_despues": org_na_despues,
        "org_encontrados": org_na_antes - org_na_despues,
    }

    print(f"\n  📊 Resultados LLM:")
    print(f"     no_psy: {no_psy_antes} → {no_psy_despues} ({stats['reclasificados']} reclasificados)")
    print(f"     email N/A: {email_na_antes} → {email_na_despues} ({stats['email_encontrados']} encontrados)")
    print(f"     org N/A: {org_na_antes} → {org_na_despues} ({stats['org_encontrados']} encontrados)")
    print(f"  💾 CSV actualizado: {csv_path}")

    return stats


if __name__ == "__main__":
    c = get_client()
    print("Ollama disponible:", c.is_available())
    if c.is_available():
        # Test individual functions
        test = "Forest Psytrance Festival 15-17 Agosto @ Bosque Magico, Barcelona. Darkpsy, hitech, forest vibes. Org: darkforestcrew"
        print("Extract:", extract_events_llm(test))
        print("Spam:", classify_spam_llm(test))
        print("Norm loc:", normalize_location_llm("BCN"))
        print()
        # Test batch reclasificación
        test_events = [
            {"nombre": "TECHNO NIGHT BERLIN", "fuente": "Resident Advisor", "lugar": "Berlin", "organizador": "Tresor", "subgenero": "no_psy"},
            {"nombre": "Dark Forest Ritual", "fuente": "Resident Advisor", "lugar": "Amsterdam", "organizador": "Forest Crew", "subgenero": "no_psy"},
        ]
        result = reclasificar_no_psy(test_events)
        for e in result:
            print(f"  {e['nombre'][:40]} → {e['subgenero']}")