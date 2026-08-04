#!/usr/bin/env python3
"""
Improvement Loop — Patrón AgenteQwen (probado, simple, efectivo).
- Main Agent: orquesta loop, analiza métricas, pide a Qwen mejoras
- Workers paralelos: ProcessPoolExecutor para scrapers
- Sub-agentes: enriquecimiento, validación, etc.
- Config dict simple, historial JSON, Qwen propone JSON improvements
"""

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import ollama
except ImportError:
    ollama = None

# Importar sub-agentes existentes
try:
    from scrapers.enriquecer_eventos import enriquecer_eventos_con_qwen
    ENRIQUECIMIENTO_DISPONIBLE = True
except Exception:
    ENRIQUECIMIENTO_DISPONIBLE = False

try:
    from scrapers.anti_block import get_anti_block
except Exception:
    get_anti_block = None

MODELO_QWEN = "qwen2.5-coder:7b"
MAX_WORKERS = 2
HISTORIAL_FILE = str(Path(_PROJECT_ROOT) / "historial_improvement_loop.json")
MEJOR_CONFIG_FILE = str(Path(_PROJECT_ROOT) / "mejor_config_ig.json")


def consultar_qwen(prompt: str, modelo: str = MODELO_QWEN) -> str:
    """Consulta simple a Qwen local via Ollama."""
    if not ollama:
        return "{}"
    try:
        resp = ollama.chat(model=modelo, messages=[{"role": "user", "content": prompt}])
        return resp["message"]["content"]
    except Exception as e:
        print(f"⚠️ Qwen error: {e}")
        return "{}"


def extraer_json(texto: str) -> Optional[Dict]:
    """Extrae primer JSON válido del texto."""
    if not texto:
        return None
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    try:
        return json.loads(texto)
    except Exception:
        return None


def analizar_resultados(eventos: List[Dict], tiempo_total: float) -> Dict:
    """Métricas clave para decisión de Qwen."""
    if not eventos:
        return {"eventos": 0, "fuentes": 0, "organizadores": 0, "con_descripcion": 0, "score": 0, "tiempo": round(tiempo_total, 1)}

    fuentes = set(e.get("fuente", "") for e in eventos)
    orgs = sum(1 for e in eventos if e.get("organizador") and e["organizador"] not in ("", "Desconocido", "No disponible"))
    con_desc = sum(1 for e in eventos if e.get("descripcion") and len(e["descripcion"]) > 20)

    return {
        "eventos": len(eventos),
        "fuentes": len(fuentes),
        "organizadores": orgs,
        "con_descripcion": con_desc,
        "score": len(eventos) + (orgs * 3) + (con_desc * 2) + (len(fuentes) * 5),
        "tiempo": round(tiempo_total, 1),
    }


def guardar_historial(historial: List[Dict]):
    try:
        with open(HISTORIAL_FILE, "w") as f:
            json.dump(historial, f, indent=2, default=str)
    except Exception:
        pass


def cargar_historial() -> List[Dict]:
    if Path(HISTORIAL_FILE).exists():
        try:
            with open(HISTORIAL_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return []


def guardar_mejor_config(config: Dict):
    try:
        with open(MEJOR_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass


def cargar_mejor_config() -> Optional[Dict]:
    if Path(MEJOR_CONFIG_FILE).exists():
        try:
            with open(MEJOR_CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ============================================================
# ARQUITECTURAS DE SCRAPING A TESTEAR
# ============================================================
ARQUITECTURAS = {
    "requests_only": {
        "nombre": "Solo Requests (rápido)",
        "usar_playwright": False,
        "usar_tor": True,
        "usar_llm_extract": True,
        "max_posts_per_tag": 3,
        "timeout_tag": 15,
    },
    "playwright_only": {
        "nombre": "Solo Playwright (robusto)",
        "usar_playwright": True,
        "usar_tor": True,
        "usar_llm_extract": True,
        "max_posts_per_tag": 2,
        "timeout_tag": 45,
    },
    "hibrido_requests_pw": {
        "nombre": "Híbrido: Requests → Playwright fallback",
        "usar_playwright": True,
        "usar_tor": True,
        "usar_llm_extract": True,
        "max_posts_per_tag": 3,
        "timeout_tag": 30,
    },
    "sin_tor_requests": {
        "nombre": "Requests sin Tor (IP real, rápido)",
        "usar_playwright": False,
        "usar_tor": False,
        "usar_llm_extract": True,
        "max_posts_per_tag": 5,
        "timeout_tag": 10,
    },
    "sin_tor_playwright": {
        "nombre": "Playwright sin Tor (IP real, robusto)",
        "usar_playwright": True,
        "usar_tor": False,
        "usar_llm_extract": True,
        "max_posts_per_tag": 2,
        "timeout_tag": 40,
    },
    "con_llm_vision": {
        "nombre": "Con LLM Vision (screenshot + Qwen)",
        "usar_playwright": True,
        "usar_tor": True,
        "usar_llm_extract": True,
        "usar_vision": True,
        "max_posts_per_tag": 1,
        "timeout_tag": 60,
    },
    "fusion_scraper": {
        "nombre": "Fusion Scraper (6 estrategias combinadas)",
        "usar_playwright": True,
        "usar_tor": True,
        "usar_llm_extract": True,
        "max_posts_per_tag": 20,
        "timeout_tag": 120,
        "fusion_mode": True,
    },
}
def worker_scrape_hashtag(args: Dict) -> Dict:
    """
    Worker que se ejecuta en proceso separado.
    args: {hashtag, arch_config, config, worker_id, arch_name}
    Returns: {hashtag, eventos, success, error, arch_name}
    """
    hashtag = args["hashtag"]
    arch_config = args["arch_config"]
    config = args["config"]
    worker_id = args["worker_id"]
    arch_name = args.get("arch_name", "unknown")

    try:
        sys.path.insert(0, _PROJECT_ROOT)

        # Fusion mode: usar el fusion scraper completo
        if arch_config.get("fusion_mode", False):
            from scrapers.instagram_fusion import scrape_instagram_fusion
            h = hashtag  # ya viene limpio
            t0 = time.time()
            eventos = scrape_instagram_fusion(
                hashtags=[h],
                max_posts_per_tag=arch_config.get("max_posts_per_tag", 20),
                use_tor=arch_config.get("usar_tor", True),
                estrategias_habilitadas=arch_config.get("fusion_estrategias", [
                    "curl_cffi_tls", "requests_stealth", "playwright_stealth", "serp_fallback"
                ]),
                parallel=arch_config.get("fusion_parallel", False),
                max_workers=arch_config.get("fusion_max_workers", 2),
            )
            return {
                "hashtag": hashtag,
                "eventos": eventos,
                "success": True,
                "worker_id": worker_id,
                "arch": arch_name,
                "tiempo": round(time.time() - t0, 1),
            }

        # Resto de arquitecturas (original)
        from scrapers.instagram_scraper import _scrape_hashtag_requests, _eventos_del_post, _limpiar_hashtag, _parse_html_posts
        from scrapers.agents import run_ingest_pw

        h = _limpiar_hashtag(hashtag)
        if not h:
            return {"hashtag": hashtag, "eventos": [], "success": False, "error": "hashtag vacío", "arch": arch_name}

        t0 = time.time()
        eventos = []

        if arch_config.get("usar_playwright", False):
            # Playwright path
            url = f"https://www.instagram.com/explore/tags/{h}/"
            html = asyncio.run(run_ingest_pw(url, wait_selector="article", use_tor=arch_config.get("usar_tor", True)))
            if html:
                posts = _parse_html_posts(html)
                for p in posts[:arch_config.get("max_posts_per_tag", 2)]:
                    evs = _eventos_del_post(p.get("url", ""), p.get("caption", ""), p.get("location", ""), h)
                    if evs:
                        eventos.extend(evs)
        else:
            # Requests path
            posts = _scrape_hashtag_requests(h, t0)
            if posts:
                for p in posts[:arch_config.get("max_posts_per_tag", 3)]:
                    evs = _eventos_del_post(p.get("url", ""), p.get("caption", ""), p.get("location", ""), h)
                    if evs:
                        eventos.extend(evs)

        return {
            "hashtag": hashtag,
            "eventos": eventos,
            "success": True,
            "worker_id": worker_id,
            "arch": arch_name,
            "tiempo": round(time.time() - t0, 1),
        }
    except Exception as e:
        return {"hashtag": hashtag, "eventos": [], "success": False, "error": str(e), "worker_id": worker_id, "arch": arch_name}


# ============================================================
# SUB-AGENTE: Enriquecimiento (ya existe en enriquecer_eventos.py)
# ============================================================
async def subagent_enriquecer(eventos: List[Dict]) -> List[Dict]:
    """Wrapper async para enriquecimiento paralelo."""
    if not ENRIQUECIMIENTO_DISPONIBLE or not eventos:
        return eventos
    try:
        return await enriquecer_eventos_con_qwen(eventos)
    except Exception as e:
        print(f"⚠️ Sub-agente enriquecimiento falló: {e}")
        return eventos


# ============================================================
# MAIN AGENT: Orquesta todo el loop
# ============================================================
class ImprovementAgent:
    def __init__(
        self,
        output_file: str = "eventos_instagram.json",
        max_hashtags: int = 20,
        max_posts_per_tag: int = 3,
        timeout_total: int = 180,
        use_tor: bool = True,
    ):
        self.output_file = output_file
        self.config = {
            "max_hashtags": max_hashtags,
            "max_posts_per_tag": max_posts_per_tag,
            "timeout_total": timeout_total,
            "use_tor": use_tor,
            "priorizar_hashtags_productivos": True,
        }
        self.historial = cargar_historial()
        self.mejor_config = cargar_mejor_config() or {}
        self.mejor_score = -1  # -1 para que score 0 sea mejor
        self.ab = get_anti_block() if get_anti_block else None

        # Aplicar mejor config si existe
        if self.mejor_config:
            self.config.update(self.mejor_config)
            print(f"📌 Cargada mejor config anterior: {self.mejor_config}")

    def _generar_hashtags(self) -> List[str]:
        """Genera lista de hashtags (reutiliza lógica existente)."""
        from scrapers.instagram_scraper import _build_hashtags, _load_config
        cfg = _load_config()
        hashtags = _build_hashtags(cfg)
        return hashtags[:self.config["max_hashtags"]]

    def _ejecutar_paralelo(self, hashtags: List[str], archs_a_testear: Optional[List[str]] = None) -> List[Dict]:
        """
        Ejecuta workers en paralelo para múltiples hashtags Y arquitecturas.
        Si archs_a_testear es None, usa la arquitectura actual del config.
        Retorna lista de resultados con campo 'arch' indicando la arquitectura usada.
        """
        if archs_a_testear is None:
            # Modo simple: una arquitectura (comportamiento original)
            arch_actual = {
                "usar_playwright": self.config.get("usar_playwright", False),
                "usar_tor": self.config.get("use_tor", True),
                "max_posts_per_tag": self.config.get("max_posts_per_tag", 3),
            }
            return self._ejecutar_una_arquitectura(hashtags, arch_actual, "actual")

        # Modo comparativo: testear múltiples arquitecturas
        print(f"🧪 TESTEANDO {len(archs_a_testear)} ARQUITECTURAS en paralelo...")
        print(f"   Arquitecturas: {', '.join(archs_a_testear)}")

        todos_resultados = []

        for arch_name in archs_a_testear:
            if arch_name not in ARQUITECTURAS:
                print(f"   ⚠️ Arquitectura desconocida: {arch_name}")
                continue
            arch_config = ARQUITECTURAS[arch_name]
            print(f"\n   🏗️  Probando: {arch_config['nombre']} ({arch_name})")
            resultados = self._ejecutar_una_arquitectura(hashtags, arch_config, arch_name)
            todos_resultados.extend(resultados)

        return todos_resultados

    def _ejecutar_una_arquitectura(self, hashtags: List[str], arch_config: Dict, arch_name: str) -> List[Dict]:
        """Ejecuta una arquitectura específica para todos los hashtags."""
        print(f"      🚀 {min(MAX_WORKERS, len(hashtags))} workers × {len(hashtags)} hashtags")

        worker_args = []
        for i, h in enumerate(hashtags):
            worker_args.append({
                "hashtag": h,
                "arch_config": arch_config,
                "config": self.config,
                "worker_id": i % MAX_WORKERS,
                "arch_name": arch_name,
            })

        resultados = []
        with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(worker_scrape_hashtag, args): args["hashtag"] for args in worker_args}
            for future in as_completed(futures):
                try:
                    res = future.result(timeout=arch_config.get("timeout_tag", 30) + 10)
                    resultados.append(res)
                    if res["success"]:
                        print(f"         ✅ #{res['hashtag']}: {len(res['eventos'])} eventos ({res['tiempo']}s) [{res['arch']}]")
                    else:
                        print(f"         ❌ #{res['hashtag']}: {res.get('error', 'error')} [{res['arch']}]")
                except Exception as e:
                    hashtag = futures[future]
                    print(f"         ❌ #{hashtag}: worker exception: {e} [{arch_name}]")
                    resultados.append({"hashtag": hashtag, "eventos": [], "success": False, "error": str(e), "arch": arch_name})

        return resultados

    def _consolidar_eventos(self, resultados: List[Dict]) -> List[Dict]:
        """Consolida eventos de todos los workers, dedup simple."""
        todos = []
        for r in resultados:
            if r["success"] and r["eventos"]:
                todos.extend(r["eventos"])

        # Dedup por nombre+fecha+ciudad
        vistos = set()
        unicos = []
        for ev in todos:
            key = (
                (ev.get("nombre", "").strip().lower()),
                (ev.get("fecha", "").strip()),
                (ev.get("ciudad", "").strip().lower()),
            )
            if key not in vistos:
                vistos.add(key)
                unicos.append(ev)

        # Guardar
        if unicos:
            try:
                with open(self.output_file, "w") as f:
                    json.dump(unicos, f, ensure_ascii=False, indent=2)
                print(f"💾 {len(unicos)} eventos únicos guardados en {self.output_file}")
            except Exception as e:
                print(f"⚠️ Error guardando: {e}")

        return unicos

    def _qwen_propone_mejora(self, metricas: Dict) -> Optional[Dict]:
        """Pide a Qwen una mejora concreta en JSON (incluye cambio de arquitectura)."""
        arch_actual = self.mejor_config.get("mejor_arquitectura", "hibrido_requests_pw")
        prompt = f"""
Eres un agente experto en scraping de Instagram para eventos psytrance.
Analiza estos resultados y propone UNA mejora CONCRETA para subir el score.

RESULTADOS ACTUALES:
- Eventos totales: {metricas['eventos']}
- Fuentes únicas: {metricas['fuentes']}
- Organizadores extraídos: {metricas['organizadores']}
- Eventos con descripción: {metricas['con_descripcion']}
- Tiempo total: {metricas['tiempo']}s
- Score: {metricas['score']}
- Arquitectura actual: {arch_actual}

CONFIG ACTUAL:
{json.dumps(self.config, indent=2)}

ARQUITECTURAS DISPONIBLES:
{json.dumps({k: v['nombre'] for k, v in ARQUITECTURAS.items()}, indent=2)}

REGLAS:
- Si pocos organizadores (<10) pero eventos (>10) → ENRIQUECER_EVENTOS
- Si pocos eventos (<5) → CAMBIAR_ARQUITECTURA (a más agresiva) o AUMENTAR_HASHTAGS
- Si score bajo → CAMBIAR_ARQUITECTURA o AUMENTAR_TIMEOUT
- Si mucho tiempo (>150s) → CAMBIAR_ARQUITECTURA (a más rápida) o REDUCIR_HASHTAGS
- Si la arquitectura actual falla consistentemente → CAMBIAR_ARQUITECTURA

Responde SOLO en JSON:
{{
  "accion": "ENRIQUECER_EVENTOS | CAMBIAR_ARQUITECTURA | AUMENTAR_HASHTAGS | AUMENTAR_POSTS_POR_TAG | AUMENTAR_TIMEOUT | REDUCIR_HASHTAGS",
  "parametro": "nombre_parametro",
  "valor": "nuevo_valor",
  "razon": "explicación breve basada en datos"
}}
Para CAMBIAR_ARQUITECTURA, parametro="arquitectura", valor="nombre_arquitectura" (ej: "playwright_only")
"""
        resp = consultar_qwen(prompt)
        mejora = extraer_json(resp)
        if mejora and "accion" in mejora:
            print(f"🤖 Qwen propone: {mejora['razon']} → {mejora['accion']} {mejora.get('parametro', '')}={mejora.get('valor', '')}")
            return mejora
        print("⚠️ Qwen no devolvió JSON válido, usando heurística")
        return self._heuristica_mejora(metricas)

    def _heuristica_mejora(self, metricas: Dict) -> Dict:
        """Fallback simple si Qwen falla."""
        arch_actual = self.mejor_config.get("mejor_arquitectura", "hibrido_requests_pw") if self.mejor_config else "hibrido_requests_pw"
        
        if metricas["organizadores"] < 10 and metricas["eventos"] > 10:
            return {"accion": "ENRIQUECER_EVENTOS", "parametro": "", "valor": "true", "razon": "Pocos organizadores, enriquecer"}
        if metricas["eventos"] < 5:
            # Cambiar a arquitectura más agresiva si la actual no da resultados
            if arch_actual == "requests_only":
                return {"accion": "CAMBIAR_ARQUITECTURA", "parametro": "arquitectura", "valor": "hibrido_requests_pw", "razon": "Requests solo no da resultados, probar híbrido"}
            elif arch_actual == "hibrido_requests_pw":
                return {"accion": "CAMBIAR_ARQUITECTURA", "parametro": "arquitectura", "valor": "playwright_only", "razon": "Híbrido no da resultados, probar Playwright solo"}
            return {"accion": "AUMENTAR_HASHTAGS", "parametro": "max_hashtags", "valor": str(min(self.config["max_hashtags"] + 5, 30)), "razon": "Pocos eventos, más hashtags"}
        if metricas["tiempo"] > 150:
            # Cambiar a arquitectura más rápida
            if arch_actual in ("playwright_only", "con_llm_vision"):
                return {"accion": "CAMBIAR_ARQUITECTURA", "parametro": "arquitectura", "valor": "requests_only", "razon": "Muy lento, cambiar a requests_only"}
            return {"accion": "REDUCIR_HASHTAGS", "parametro": "max_hashtags", "valor": str(max(self.config["max_hashtags"] - 5, 10)), "razon": "Muy lento, reducir hashtags"}
        return {"accion": "AUMENTAR_POSTS_POR_TAG", "parametro": "max_posts_per_tag", "valor": str(min(self.config["max_posts_per_tag"] + 1, 5)), "razon": "Más posts por hashtag"}

    def _aplicar_mejora(self, mejora: Dict):
        """Aplica la mejora al config."""
        accion = mejora.get("accion", "")
        param = mejora.get("parametro", "")
        valor = mejora.get("valor", "")

        try:
            if accion == "ENRIQUECER_EVENTOS":
                # Se ejecuta como sub-agente aparte
                pass
            elif accion == "CAMBIAR_ARQUITECTURA" and param == "arquitectura":
                if valor in ARQUITECTURAS:
                    nueva_arch = ARQUITECTURAS[valor]
                    self.config.update({
                        "usar_playwright": nueva_arch.get("usar_playwright", False),
                        "use_tor": nueva_arch.get("usar_tor", True),
                        "max_posts_per_tag": nueva_arch.get("max_posts_per_tag", 3),
                    })
                    self.mejor_config["mejor_arquitectura"] = valor
                    print(f"🔄 Cambio de arquitectura: {valor} ({nueva_arch['nombre']})")
                else:
                    print(f"⚠️ Arquitectura desconocida: {valor}")
            elif accion == "AUMENTAR_HASHTAGS" and param == "max_hashtags":
                self.config["max_hashtags"] = int(valor)
            elif accion == "AUMENTAR_POSTS_POR_TAG" and param == "max_posts_per_tag":
                self.config["max_posts_per_tag"] = int(valor)
            elif accion == "AUMENTAR_TIMEOUT" and param == "timeout_total":
                self.config["timeout_total"] = int(valor)
            elif accion == "REDUCIR_HASHTAGS" and param == "max_hashtags":
                self.config["max_hashtags"] = int(valor)
            print(f"✅ Config actualizada: {param}={valor}")
        except Exception as e:
            print(f"⚠️ Error aplicando mejora: {e}")

    def run_loop(self, max_iteraciones: int = 5) -> List[Dict]:
        """Loop principal de mejora continua con comparación de arquitecturas."""
        print("=" * 60)
        print("🔄 IMPROVEMENT LOOP INSTAGRAM (Patrón AgenteQwen + Arquitecturas)")
        print("=" * 60)
        print(f"📌 Output: {self.output_file}")
        print(f"📌 Max hashtags: {self.config['max_hashtags']}")
        print(f"📌 Max posts/tag: {self.config['max_posts_per_tag']}")
        print(f"📌 Timeout: {self.config['timeout_total']}s")
        print(f"📌 Tor: {self.config['use_tor']}")
        print(f"📌 Enriquecimiento: {'✅' if ENRIQUECIMIENTO_DISPONIBLE else '❌'}")
        print(f"📌 Historial previo: {len(self.historial)} iteraciones")
        print(f"📌 Arquitecturas disponibles: {len(ARQUITECTURAS)}")
        for k, v in ARQUITECTURAS.items():
            print(f"     - {k}: {v['nombre']}")
        print("=" * 60)

        mejores_eventos = []
        mejor_arch = None
        metricas_por_arch = {}

        for iteracion in range(1, max_iteraciones + 1):
            print(f"\n📌 ITERACIÓN {iteracion}/{max_iteraciones}")
            print("-" * 40)
            t_iter = time.time()

            # 1. Generar hashtags
            hashtags = self._generar_hashtags()
            print(f"   Hashtags a procesar: {len(hashtags)}")

            # 2. ESTRATEGIA: Iteración 1 = testear arquitecturas, resto = usar mejor
            if iteracion == 1 and len(ARQUITECTURAS) > 1:
                # Testear TODAS las arquitecturas en la primera iteración
                archs_a_testear = list(ARQUITECTURAS.keys())
                print(f"   🧪 MODO COMPARATIVO: testeando {len(archs_a_testear)} arquitecturas")
                resultados = self._ejecutar_paralelo(hashtags, archs_a_testear)

                # Agrupar resultados por arquitectura
                eventos_por_arch = {}
                for r in resultados:
                    arch = r.get("arch", "unknown")
                    if arch not in eventos_por_arch:
                        eventos_por_arch[arch] = []
                    if r["success"] and r["eventos"]:
                        eventos_por_arch[arch].extend(r["eventos"])

                # Evaluar cada arquitectura
                print(f"\n   📊 COMPARACIÓN DE ARQUITECTURAS:")
                for arch, evs in eventos_por_arch.items():
                    # Dedup por arquitectura
                    vistos = set()
                    unicos = []
                    for ev in evs:
                        key = (ev.get("nombre", "").strip().lower(), ev.get("fecha", "").strip(), ev.get("ciudad", "").strip().lower())
                        if key not in vistos:
                            vistos.add(key)
                            unicos.append(ev)
                    
                    metricas = analizar_resultados(unicos, 0)
                    metricas_por_arch[arch] = metricas
                    print(f"      {arch}: {metricas['eventos']} eventos, {metricas['organizadores']} orgs, score={metricas['score']}")

                # Seleccionar mejor arquitectura
                if metricas_por_arch:
                    mejor_arch = max(metricas_por_arch.keys(), key=lambda a: metricas_por_arch[a]["score"])
                    print(f"   🏆 MEJOR ARQUITECTURA: {mejor_arch} (score={metricas_por_arch[mejor_arch]['score']})")
                    
                    # Usar eventos de la mejor arquitectura
                    eventos = eventos_por_arch[mejor_arch]
                    
                    # Actualizar config con params de la mejor arquitectura
                    best_arch_config = ARQUITECTURAS[mejor_arch]
                    self.config.update({
                        "usar_playwright": best_arch_config.get("usar_playwright", False),
                        "use_tor": best_arch_config.get("usar_tor", True),
                        "max_posts_per_tag": best_arch_config.get("max_posts_per_tag", 3),
                    })
                else:
                    print("   ⚠️ Ninguna arquitectura produjo eventos, usando híbrido por defecto")
                    mejor_arch = "hibrido_requests_pw"
                    resultados = self._ejecutar_paralelo(hashtags, ["hibrido_requests_pw"])
                    eventos = []
                    for r in resultados:
                        if r["success"] and r["eventos"]:
                            eventos.extend(r["eventos"])
            else:
                # Usar la mejor arquitectura conocida
                archs_a_usar = [mejor_arch] if mejor_arch else ["hibrido_requests_pw"]
                print(f"   🎯 Usando arquitectura: {archs_a_usar[0]}")
                resultados = self._ejecutar_paralelo(hashtags, archs_a_usar)
                eventos = []
                for r in resultados:
                    if r["success"] and r["eventos"]:
                        eventos.extend(r["eventos"])

            # 3. Consolidar (dedup global)
            eventos = self._consolidar_eventos([{"success": True, "eventos": eventos}])

            # 4. Sub-agente enriquecimiento (si corresponde)
            if ENRIQUECIMIENTO_DISPONIBLE and eventos:
                print(f"   🔍 Enriqueciendo {len(eventos)} eventos...")
                eventos = asyncio.run(subagent_enriquecer(eventos))
                # Re-guardar enriquecidos
                try:
                    with open(self.output_file, "w") as f:
                        json.dump(eventos, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

            # 5. Métricas
            tiempo_iter = time.time() - t_iter
            metricas = analizar_resultados(eventos, tiempo_iter)
            metricas["arquitectura"] = mejor_arch or "desconocida"
            print(f"\n📊 MÉTRICAS:")
            for k, v in metricas.items():
                print(f"   {k}: {v}")

            # 6. Guardar historial
            self.historial.append({
                "iteracion": iteracion,
                "config": self.config.copy(),
                "metricas": metricas,
                "metricas_por_arch": metricas_por_arch if iteracion == 1 else {},
                "timestamp": time.time(),
            })
            guardar_historial(self.historial)

            # 7. Actualizar mejor
            if metricas["score"] > self.mejor_score:
                self.mejor_score = metricas["score"]
                self.mejor_config = self.config.copy()
                self.mejor_config["mejor_arquitectura"] = mejor_arch
                guardar_mejor_config(self.mejor_config)
                mejores_eventos = eventos
                print(f"⭐ NUEVO MEJOR SCORE: {self.mejor_score} (arch: {mejor_arch})")

            # 8. Verificar objetivo
            if metricas["eventos"] >= 20 and metricas["organizadores"] >= 10:
                print(f"\n🎉 ¡OBJETIVO ALCANZADO! ({metricas['eventos']} eventos, {metricas['organizadores']} orgs)")
                break

            # 9. Qwen propone mejora para siguiente iteración
            if iteracion < max_iteraciones:
                mejora = self._qwen_propone_mejora(metricas)
                if mejora:
                    self._aplicar_mejora(mejora)

            print(f"   ⏳ Iteración completada en {tiempo_iter:.0f}s")

        # Resultado final
        print("\n" + "=" * 60)
        print("🏆 LOOP FINALIZADO")
        print("=" * 60)
        print(f"Mejor score: {self.mejor_score}")
        mejor_arch_final = self.mejor_config.get("mejor_arquitectura", "N/A") if self.mejor_config else "N/A"
        print(f"Mejor arquitectura: {mejor_arch_final}")
        print(f"Mejor config: {self.mejor_config}")
        print(f"Total iteraciones: {len(self.historial)}")

        return mejores_eventos


def run_improvement_loop(
    hashtags: Optional[List[str]] = None,
    output_file: str = "eventos_instagram.json",
    max_hashtags: int = 20,
    max_posts_per_tag: int = 3,
    timeout_total: int = 180,
    use_tor: bool = True,
    max_iteraciones: int = 5,
) -> List[Dict]:
    """Función de conveniencia para usar desde scrapers existentes."""
    agent = ImprovementAgent(
        output_file=output_file,
        max_hashtags=max_hashtags,
        max_posts_per_tag=max_posts_per_tag,
        timeout_total=timeout_total,
        use_tor=use_tor,
    )
    # Si se pasan hashtags específicos, usarlos en lugar de generarlos
    if hashtags:
        agent.config["max_hashtags"] = len(hashtags)
        # Monkey-patch para usar hashtags dados
        original_generar = agent._generar_hashtags
        agent._generar_hashtags = lambda: hashtags[:agent.config["max_hashtags"]]

    return agent.run_loop(max_iteraciones=max_iteraciones)


if __name__ == "__main__":
    # Test rápido
    test_tags = ["psytrance", "darkpsy", "forestpsytrance"]
    eventos = run_improvement_loop(
        hashtags=test_tags,
        output_file="/tmp/test_ig_loop.json",
        max_hashtags=3,
        max_posts_per_tag=2,
        timeout_total=60,
        use_tor=False,
        max_iteraciones=2,
    )
    print(f"\n✅ Test completado: {len(eventos)} eventos finales")