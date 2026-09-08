#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP clasificador — Server MCP estándar (JSON-RPC sobre stdio, sin SDK).

Expone tools de CLASIFICACIÓN acelerada para el Dharmadhatu Bot usando el
LLM rápido (freellmpool directo ~2s → fallback llm_hibrido → regex).

Tools:
  - clasificar_subgeneros(batch)  : subgénero por evento (LLM rápido)
  - clasificar_spam(texto)        : EVENTO | RUIDO
  - sugerir_organizador(evento)   : candidato + confianza (no escribe)
  - estado_dataset()              : cobertura org/email/no_psy del CSV
  - reclasificar_no_psy(limite)   : procesa no_psy con checkpoint y escribe CSV

Protocolo MCP: initialize / notifications/initialized / ping / tools/list /
tools/call (nuevas líneas JSON-RPC 2.0 sobre stdin/stdout).

Uso:
  python3 core/mcp_clasificar.py            # server MCP (stdio)
  python3 core/mcp_clasificar.py --test     # self-test de las tools
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SERVER_NAME = "dharmadhatu-clasificador"
SERVER_VERSION = "1.0.0"
CSV_PATH = os.path.join(_ROOT, "eventos_encontrados.csv")
CHECKPOINT_PATH = os.path.join(_ROOT, "llm_checkpoint.json")

SG_VALIDOS = {
    "psytrance", "darkpsy", "forest", "hitech", "progressive",
    "fullon", "goa", "psychill", "psybient", "suomisaundi",
    "twilight", "psychedelic",
}

FREELLMPOOL_URL = os.getenv("FREELLMPOOL_URL", "http://localhost:8080/v1/chat/completions")
FREEMODEL = os.getenv("FREEMODEL", "quality")


# ---------------------------------------------------------------------------
# LLM rápido: freellmpool directo (urllib) con fallback al híbrido
# ---------------------------------------------------------------------------
def _freellmpool_direct(prompt: str, system: str, temperature: float = 0.05) -> Optional[str]:
    api_key = os.getenv("FREELLMPOOL_PROXY_KEY", "")
    if not api_key:
        return None
    payload = {
        "model": FREEMODEL,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    req = __import__("urllib.request", fromlist=["Request"]).Request(
        FREELLMPOOL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with __import__("urllib.request", fromlist=["urlopen"]).urlopen(req, timeout=45) as r:
            body = json.loads(r.read().decode("utf-8"))
        return (body.get("choices") or [{}])[0].get("message", {}).get("content", "").strip() or None
    except Exception:
        return None


def _llm_generate(prompt: str, system: str = "", temperature: float = 0.05) -> Optional[str]:
    """freellmpool directo primero (rápido), después hybrid (rotación anti-bloqueo)."""
    resp = _freellmpool_direct(prompt, system, temperature)
    if resp:
        return resp
    try:
        from core.llm_hibrido import get_hybrid_client
        return get_hybrid_client().generate(prompt, system=system, temperature=temperature)
    except Exception:
        return None


def _clean_json_array(resp: str) -> Optional[List]:
    if not resp:
        return None
    clean = resp.strip()
    if "```" in clean:
        clean = re.sub(r"```(?:json)?", "", clean).strip()
    m = re.search(r"\[.*\]", clean, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def tool_clasificar_subgeneros(eventos: List[Dict]) -> Dict:
    """Clasifica subgénero de cada evento. Entrada: [{nombre, lugar, organizador, fuente}]."""
    if not isinstance(eventos, list) or not eventos:
        return {"ok": False, "error": "eventos vacío o inválido"}
    batch_size = 5
    resultados: List[Dict] = []
    client_tmp = None
    from core.llm_hibrido import get_hybrid_client
    client_tmp = get_hybrid_client()
    for i in range(0, len(eventos), batch_size):
        batch = eventos[i:i + batch_size]
        lineas = [
            f"{j}: {e.get('nombre','')[:80]} @ {e.get('lugar','')[:40]}"
            for j, e in enumerate(batch)
        ]
        prompt = (
            "Eventos musicales. Clasifica cada uno en un subgénero:\n"
            + "\n".join(lineas)
            + "\nSubgeneros: psytrance,darkpsy,forest,hitech,progressive,"
              "fullon,goa,psychill,psybient,suomisaundi,twilight,psychedelic,NO_PSY\n"
              'Responde SOLO JSON array: ["sg0","sg1",...]'
        )
        resp = _llm_generate(prompt, "Clasificador. Solo JSON array de strings.", 0.05)
        clasifs = _clean_json_array(resp) if resp else None
        for j, ev in enumerate(batch):
            sg = ""
            if clasifs and j < len(clasifs):
                sg = str(clasifs[j]).strip().lower()
                if sg not in SG_VALIDOS:
                    sg = ""
            if not sg:
                sg = _fallback_regex_clasificar(ev.get("nombre", ""), ev.get("organizador", ""))
            resultados.append({"nombre": ev.get("nombre", ""), "subgenero": sg})
    return {"ok": True, "resultados": resultados, "total": len(resultados)}


def tool_clasificar_spam(texto: str) -> Dict:
    """True si es RUIDO; False si es EVENTO."""
    if not texto or len(texto.strip()) < 20:
        return {"ok": True, "ruido": True, "clase": "RUIDO"}
    prompt = (
        "Clasifica si este texto describe un EVENTO REAL de psytrance con fecha/lugar "
        "concretos, o RUIDO (promo genérica, meme, venta sin fecha, 'próximamente').\n\n"
        f"Texto: {texto[:1500]}\n\nResponde SOLO: EVENTO o RUIDO."
    )
    resp = _llm_generate(prompt, "Clasificador binario. Solo EVENTO o RUIDO.", 0.05)
    clase = "RUIDO" if (not resp or "RUIDO" in resp.upper()) else "EVENTO"
    return {"ok": True, "ruido": clase == "RUIDO", "clase": clase}


def tool_sugerir_organizador(evento: Dict) -> Dict:
    """Sugiere organizador con confianza. NO escribe en CSV (evitar alucinaciones)."""
    nombre = (evento.get("nombre") or "")[:100]
    fuente = (evento.get("fuente") or "")[:30]
    prompt = (
        "Si el nombre del evento o la fuente revelan CLARAMENTE el organizador/colectivo "
        "(p.ej. 'XX presents YY', 'YY festival' con fuente 'YY official', página de @CrewName), "
        "devuélvelo. "
        "Si es inventado, ambiguo, genérico o es un SUBGÉNERO (darkpsy, forest, goa, psytrance, "
        "progressive, zenon, etc.), responde N/A.\n"
        f"Evento: {nombre}\nFuente: {fuente}\n"
        'Responde solo JSON: {"organizador": "...", "confianza": "alta|media|baja"}'
    )
    resp = _llm_generate(prompt, "Extractor de organizadores. No inventar. Solo JSON.", 0.05)
    try:
        m = re.search(r"\{.*\}", resp or "", re.DOTALL)
        data = json.loads(m.group(0)) if m else None
        org = (data or {}).get("organizador", "").strip()
        conf = (data or {}).get("confianza", "baja").strip().lower()
        sg_tokens = (
            "darkpsy", "forest", "goa", "psytrance", "psy trance", "progressive",
            "fullon", "psychill", "psybient", "hitech", "hi-tech", "suomisaundi",
            "twilight", "psychedelic", "zenon", "open air", "festival", "event",
            "party", "rave", "ritual", "gathering",
        )
        org_l = org.lower().strip(" .")
        if org.upper() in ("N/A", "") or any(t == org_l or t in org_l.split() for t in sg_tokens):
            return {"ok": True, "organizador": "", "confianza": "baja", "usable": False,
                    "razon": "subgenero/generico"}
        ok = conf in ("alta", "media")
        return {"ok": True, "organizador": org if ok else "", "confianza": conf,
                "usable": ok, "razon": "" if ok else "confianza baja"}
    except Exception:
        return {"ok": True, "organizador": "", "confianza": "baja", "usable": False,
                "razon": "error parse"}


def _cargar_csv() -> List[Dict]:
    if not Path(CSV_PATH).exists():
        return []
    with open(CSV_PATH, encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _guardar_csv(rows: List[Dict]):
    """Escritura full-file: SOLO usar bajo el flujo con merge (ver abajo).

    Los escritores concurrentes deben fusionar sobre lectura fresca bajo
    lock (core/csv_lock) en vez de volcar snapshots stale.
    """
    keys = ["nombre", "fecha", "lugar", "pais", "continente", "subcontinente",
            "fuente", "organizador", "email", "link", "subgenero", "tipo_lugar"]
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    os.replace(tmp, CSV_PATH)


def _guardar_merge_subgenero(cambios: Dict[str, str]):
    """Aplica cambios {clave_link_o_nombre_fecha: subgenero} sobre lectura
    fresca bajo lock exclusivo. No pisa otros campos concurrentes."""
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.csv_lock import csv_locked_rows
    n = 0
    with csv_locked_rows(CSV_PATH) as (rows, _fn):
        for r in rows:
            k = r.get("link", "") or f"{r.get('nombre','')}|{r.get('fecha','')}"
            if k in cambios:
                r["subgenero"] = cambios[k]
                n += 1
    return n


def tool_estado_dataset() -> Dict:
    rows = _cargar_csv()
    total = len(rows)

    def no_na(v: str) -> bool:
        return v.strip().lower() not in ("n/a", "", "na")

    org = sum(1 for r in rows if no_na(r.get("organizador", "")))
    email = sum(1 for r in rows if no_na(r.get("email", "")))
    no_psy = sum(1 for r in rows if r.get("subgenero", "").strip() == "no_psy")
    sin_sg = sum(1 for r in rows if not r.get("subgenero", "").strip())
    return {
        "ok": True,
        "csv": CSV_PATH,
        "total": total,
        "con_organizador": org,
        "pct_organizador": round(100 * org / total, 1) if total else 0,
        "con_email": email,
        "pct_email": round(100 * email / total, 1) if total else 0,
        "no_psy": no_psy,
        "sin_subgenero": sin_sg,
    }


def _fallback_regex_clasificar(nombre: str, organizador: str = "") -> str:
    n = ((nombre or "") + " " + (organizador or "")).lower()
    if any(k in n for k in ["dark", "night", "twilight", "psycore"]):
        return "darkpsy"
    if any(k in n for k in ["forest", "organic", "woods", "jungle"]):
        return "forest"
    if any(k in n for k in ["progressive", "prog"]):
        return "progressive"
    if any(k in n for k in ["goa", "classic"]):
        return "goa"
    if any(k in n for k in ["chill", "ambient", "downtempo", "bient"]):
        return "psychill"
    if "psytrance" in n or "psy trance" in n:
        return "psytrance"
    if "hitech" in n or "hi-tech" in n:
        return "hitech"
    return ""


def tool_reclasificar_no_psy(limite: int = 200, batch_size: int = 5) -> Dict:
    """Reclasifica eventos 'no_psy' con checkpoint y escribe el CSV si hubo cambios."""
    rows = _cargar_csv()
    if not rows:
        return {"ok": False, "error": "CSV no existe"}
    no_psy = [i for i, r in enumerate(rows) if r.get("subgenero", "").strip() == "no_psy"]
    if not no_psy:
        return {"ok": True, "procesados": 0, "reclasificados": 0, "msg": "Sin no_psy"}
    if limite > 0:
        no_psy = no_psy[:limite]
    plan = [(i, rows[i]) for i in no_psy]

    stats = {"procesados": 0, "reclasificados": 0, "por_llm": 0, "por_regex": 0}
    for k in range(0, len(plan), batch_size):
        chunk = plan[k:k + batch_size]
        lineas = [
            f"{j}: {r.get('nombre','')[:80]} @ {r.get('lugar','')[:40]}"
            for j, (_, r) in enumerate(chunk)
        ]
        prompt = (
            "Eventos musicales. Clasifica cada uno:\n"
            + "\n".join(lineas)
            + "\nSubgeneros: psytrance,darkpsy,forest,hitech,progressive,"
              "fullon,goa,psychill,psybient,suomisaundi,twilight,psychedelic,NO_PSY\n"
              'Responde SOLO JSON array: ["sg0","sg1",...]'
        )
        resp = _llm_generate(prompt, "Clasificador. Solo JSON array de strings.", 0.05)
        clasifs = _clean_json_array(resp) if resp else None
        for j, (idx, r) in enumerate(chunk):
            sg = ""
            if clasifs and j < len(clasifs):
                sg = str(clasifs[j]).strip().lower()
                if sg in SG_VALIDOS:
                    r["subgenero"] = sg
                    stats["reclasificados"] += 1
                    stats["por_llm"] += 1
                    continue
            sg = _fallback_regex_clasificar(r.get("nombre", ""), r.get("organizador", ""))
            if sg:
                r["subgenero"] = sg
                stats["reclasificados"] += 1
                stats["por_regex"] += 1
        stats["procesados"] += len(chunk)

    # Merge bajo lock: solo se escribe el subgenero cambiado, sobre lectura
    # fresca. No se vuelca el snapshot (evita pisar enriquecimiento concurrente).
    cambios = {}
    for idx, r in plan:
        k = r.get("link", "") or f"{r.get('nombre','')}|{r.get('fecha','')}"
        if r.get("subgenero", "").strip() not in ("", "no_psy"):
            cambios[k] = r["subgenero"]
    _guardar_merge_subgenero(cambios)
    stats["total_no_psy_restantes"] = sum(1 for r in rows if r.get("subgenero", "").strip() == "no_psy")
    stats["ok"] = True
    return stats


TOOLS: Dict[str, Dict[str, Any]] = {
    "clasificar_subgeneros": {
        "description": "Clasifica el subgénero de cada evento en el batch (LLM rápido). Entrada: eventos=[{nombre,lugar,organizador,fuente}...]. Devuelve resultados con subgénero.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "eventos": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Lista de eventos a clasificar",
                }
            },
            "required": ["eventos"],
        },
        "fn": tool_clasificar_subgeneros,
    },
    "clasificar_spam": {
        "description": "Clasifica un texto como EVENTO (evento real con fecha/lugar) o RUIDO (promo/meme/venta sin fecha).",
        "inputSchema": {
            "type": "object",
            "properties": {"texto": {"type": "string"}},
            "required": ["texto"],
        },
        "fn": tool_clasificar_spam,
    },
    "sugerir_organizador": {
        "description": "Sugiere organizador si el nombre/fuente lo revelan claramente. No escribe en el CSV. Usa con cuidado: confianza alta/media.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "evento": {
                    "type": "object",
                    "properties": {
                        "nombre": {"type": "string"},
                        "fuente": {"type": "string"},
                    },
                }
            },
            "required": ["evento"],
        },
        "fn": tool_sugerir_organizador,
    },
    "estado_dataset": {
        "description": "Devuelve cobertura actual del CSV: total, % organizador, % email, no_psy, sin subgénero.",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": lambda **_: tool_estado_dataset(),
    },
    "reclasificar_no_psy": {
        "description": "Reclasifica eventos marcados como 'no_psy' usando LLM con fallback regex y escribe el CSV. Útil para rellenar subgéneros N/A o incorrectos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limite": {"type": "integer", "description": "Máximo de no_psy a procesar (0=todos)"},
                "batch_size": {"type": "integer", "default": 5},
            },
            "required": [],
        },
        "fn": lambda limite=200, batch_size=5: tool_reclasificar_no_psy(limite, batch_size),
    },
}


# ---------------------------------------------------------------------------
# MCP protocol (JSON-RPC 2.0 over stdio)
# ---------------------------------------------------------------------------
def _rpc_result(req_id: Any, result: Any, protocol_version: str = "2025-06-18") -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> Dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_request(msg: Dict) -> Optional[Dict]:
    """Devuelve la respuesta MCP (None para notificaciones / ping-ok)."""
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        client_version = (msg.get("params") or {}).get("protocolVersion", "2025-06-18")
        return _rpc_result(req_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }, client_version)

    if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
        return None

    if method == "ping":
        return _rpc_result(req_id, {})

    if method == "tools/list":
        tools = [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in TOOLS.items()
        ]
        return _rpc_result(req_id, {"tools": tools})

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if not spec:
            return _rpc_error(req_id, -32601, f"Tool no encontrado: {name}")
        try:
            result = spec["fn"](**args) if isinstance(args, dict) else spec["fn"](args)
            if not isinstance(result, dict):
                result = {"value": result}
            return _rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            })
        except Exception as e:
            return _rpc_result(req_id, {
                "content": [{"type": "text", "text": json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)}],
                "isError": True,
            })

    return _rpc_error(req_id, -32601, f"Method not found: {method}")


def run_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def self_test():
    test = {
        "ok": True,
        "estado": tool_estado_dataset(),
        "clasificar_spam": tool_clasificar_spam("Dark Psytrance Festival 20-22 Mar @ Bosque, con line-up completo"),
        "clasificar_subgeneros": tool_clasificar_subgeneros([
            {"nombre": "Darkpsy ritual forest night", "lugar": "Amsterdam", "organizador": ""},
            {"nombre": "Goa beach festival", "lugar": "Goa", "organizador": ""},
        ]),
    }
    print(json.dumps(test, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if "--test" in sys.argv:
        self_test()
    else:
        run_stdio()