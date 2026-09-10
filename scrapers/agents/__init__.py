#!/usr/bin/env python3
"""
Agentes Especializados — Simples, Confiables, Componibles.
Cada agente hace UNA cosa bien. Sin over-engineering.
"""

import asyncio
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from scrapers.anti_block import get_anti_block
except Exception:
    get_anti_block = None

try:
    from scrapers.vector_store import get_vector_store, Pattern
except Exception:
    get_vector_store = None
    Pattern = None

try:
    from scrapers.schemas.event_schemas import validar_evento, evento_a_dict, InstagramEvent, FacebookEvent
except Exception:
    validar_evento = evento_a_dict = None


class Trace:
    """Trace simple para observabilidad."""
    def __init__(self, agent: str, task_id: str):
        self.agent = agent
        self.task_id = task_id
        self.steps: List[Dict] = []
        self.start = time.time()

    def add(self, step: str, **kwargs):
        self.steps.append({
            "step": step,
            "ts": time.time() - self.start,
            **kwargs
        })

    def to_dict(self) -> Dict:
        return {
            "agent": self.agent,
            "task_id": self.task_id,
            "duration": time.time() - self.start,
            "steps": self.steps,
        }


class IngestAgent:
    """Fetch HTML/JSON con anti-block + Tor. Simple y robusto."""

    def __init__(self, use_tor: bool = True):
        self.use_tor = use_tor
        self.ab = get_anti_block() if get_anti_block else None

    def fetch(self, url: str, headers: Optional[Dict] = None, timeout: int = 20) -> Optional[str]:
        """Requests con proxy Tor si disponible."""
        if not self.ab:
            return None
        trace = Trace("ingest", url[:50])
        try:
            self.ab.wait_if_needed("instagram.com")
            self.ab._ensure_tor() if self.use_tor else None
            proxies = self.ab.requests_proxies() if self.use_tor else None

            import requests
            h = headers or {
                "User-Agent": self.ab.random_user_agent(),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            }
            r = requests.get(url, headers=h, timeout=timeout, proxies=proxies, allow_redirects=True)
            trace.add("request_done", status=r.status_code, len=len(r.text), redirected=r.url != url)

            if r.status_code == 200 and len(r.text) > 1000:
                trace.add("success")
                return r.text
            elif "login" in r.url or "accounts/login" in r.text.lower():
                trace.add("login_wall")
            else:
                trace.add("unexpected", status=r.status_code, len=len(r.text))
        except Exception as e:
            trace.add("error", error=str(e))
        return None

    async def fetch_playwright(self, url: str, wait_selector: str = None) -> Optional[str]:
        """Playwright con contexto stealth + Tor."""
        if not self.ab:
            return None
        trace = Trace("ingest_pw", url[:50])
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = await self.ab.create_stealth_context(browser, use_tor=self.use_tor)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if wait_selector:
                    await page.wait_for_selector(wait_selector, timeout=10000)
                content = await page.content()
                await browser.close()
                trace.add("success", len=len(content))
                return content
        except Exception as e:
            trace.add("error", error=str(e))
        return None


class ParseAgent:
    """Extracción: selectores vectoriales + fallback regex. Devuelve lista de dicts crudos."""

    def __init__(self):
        self.vs = get_vector_store() if get_vector_store else None

    def extract(self, html: str, url: str, source: str, target_desc: str) -> List[Dict]:
        """Intenta: 1) cached pattern, 2) regex fallback."""
        trace = Trace("parse", url[:50])
        results = []

        # 1. Buscar pattern vectorial
        pattern = None
        if self.vs:
            matches = self.vs.search(f"{url} {target_desc}", domain=source, top_k=1)
            if matches:
                pattern, score = matches[0]
                trace.add("pattern_hit", pattern_id=pattern.id[:8], score=score)

        # 2. Pattern cached → ejecutar selector logic (simplificado: regex sobre HTML)
        if pattern and pattern.selector_logic:
            trace.add("pattern_execute", pattern_id=pattern.id[:8])

        # 3. Fallback regex básico (event_extractor existente)
        if not results:
            trace.add("regex_fallback")
            try:
                from scrapers.event_extractor import EventExtractor
                if EventExtractor:
                    extractor = EventExtractor()
                    evs = extractor.extract_all(html, source, url) or []
                    for ev in evs:
                        ev["source_strategy"] = "regex"
                        ev["url"] = url
                        ev["fuente"] = source
                    results.extend(evs)
            except Exception:
                pass

        trace.add("done", total=len(results))
        return results

    def _save_pattern(self, url: str, target: str, strategy: str, selector: str, domain: str, results: List[Dict]):
        if not self.vs or not Pattern:
            return
        if len(results) == 0:
            return
        p = Pattern(
            url_pattern=url.split("?")[0][:200],
            target=target,
            strategy=strategy,
            selector_logic=selector,
            domain=domain,
        )
        emb = self.vs.get_embedding(f"{url} {target}")
        if emb:
            p.embedding = emb
        p.record_success()
        self.vs.add_or_update(p)


class QAAgent:
    """Validación Pydantic + confidence scoring. Filtra ruido."""

    def __init__(self, min_confidence: float = 0.6):
        self.min_confidence = min_confidence

    def validate(self, raw_events: List[Dict], source_html: str = "") -> List[Dict]:
        """Valida cada evento, asigna confidence, filtra < min_confidence."""
        trace = Trace("qa", f"{len(raw_events)} events")
        validated = []

        for ev in raw_events:
            try:
                # Validar schema según fuente
                validated_ev = validar_evento(ev) if validar_evento else None
                if not validated_ev:
                    continue

                # Confidence scoring
                conf = self._score_confidence(validated_ev, source_html)
                validated_ev.confidence = conf

                if conf >= self.min_confidence:
                    validated.append(evento_a_dict(validated_ev) if evento_a_dict else validated_ev.model_dump())
                else:
                    trace.add("low_confidence", nombre=validated_ev.nombre[:30], conf=conf)
            except Exception as e:
                trace.add("validation_error", error=str(e))

        trace.add("passed", count=len(validated))
        return validated

    def _score_confidence(self, ev, source_html: str) -> float:
        """Scoring simple: base 0.5 + bonuses por checks."""
        conf = 0.5

        # Bonus: campos completos
        if ev.nombre and len(ev.nombre) > 5:
            conf += 0.1
        if ev.fecha != "N/A":
            conf += 0.1
        if ev.lugar and ev.lugar != "N/A":
            conf += 0.1
        if ev.ciudad and ev.ciudad != "N/A":
            conf += 0.05
        if ev.subgenero and ev.subgenero.value != "":
            conf += 0.1
        if ev.organizador and ev.organizador != "Desconocido":
            conf += 0.05

        # Bonus: URL válida de la plataforma
        url_str = str(ev.url)
        if "instagram.com" in url_str and ev.fuente == "instagram":
            conf += 0.1
        elif "facebook.com" in url_str and "facebook" in ev.fuente:
            conf += 0.1

        # Penalty: spam detectado por LLM
        if classify_spam_llm and source_html:
            if classify_spam_llm(source_html[:2000]):
                conf -= 0.3

        return max(0.0, min(1.0, conf))


class RetryAgent:
    """Recovery strategies ordenadas por fitness histórico por dominio."""

    STRATEGIES = [
        ("act_interact", "Dismiss modales, scroll, click 'ver más'"),
        ("extract_refined", "Re-extract con instrucciones enriquecidas"),
        ("vision_fallback", "Screenshot + análisis visual (futuro)"),
    ]

    def __init__(self):
        self.vs = get_vector_store() if get_vector_store else None
        self.ab = get_anti_block() if get_anti_block else None

    def recover(self, url: str, html: str, domain: str, failed_strategy: str) -> Optional[str]:
        """Intenta estrategias en orden hasta obtener HTML utilizable."""
        trace = Trace("retry", url[:50])

        # Ordenar por fitness histórico (simplificado: orden fijo por ahora)
        for strategy_name, desc in self.STRATEGIES:
            if strategy_name == failed_strategy:
                continue
            trace.add("try_strategy", strategy=strategy_name)
            new_html = self._apply_strategy(strategy_name, url, html, domain)
            if new_html and len(new_html) > len(html) * 0.5:  # mejroa significativa
                trace.add("recovery_success", strategy=strategy_name, new_len=len(new_html))
                return new_html

        trace.add("all_failed")
        return None

    def _apply_strategy(self, strategy: str, url: str, html: str, domain: str) -> Optional[str]:
        if strategy == "act_interact" and self.ab:
            # Playwright: dismiss modales, scroll
            try:
                from playwright.async_api import async_playwright
                async def _act():
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                        context = await self.ab.create_stealth_context(browser, use_tor=True)
                        page = await context.new_page()
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        # Dismiss común
                        for sel in ['button:has-text("Accept")', 'button:has-text("Aceptar")', '[aria-label="Close"]', '.modal-close']:
                            try:
                                await page.click(sel, timeout=2000)
                            except Exception:
                                pass
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await page.wait_for_timeout(2000)
                        content = await page.content()
                        await browser.close()
                        return content
                return asyncio.run(_act())
            except Exception:
                pass

        elif strategy == "extract_refined":
            # Re-extract con prompt enriquecido (delegado a ParseAgent)
            return html  # señal para re-intentar parse

        return None


class IntegratorAgent:
    """Dedup semántico + write idempotente a JSON."""

    def __init__(self, output_file: str):
        self.output_file = Path(output_file)
        self.existing: Dict[str, Dict] = {}  # key: nombre|fecha|ciudad

    def load_existing(self):
        if self.output_file.exists():
            try:
                with open(self.output_file) as f:
                    data = json.load(f)
                for ev in data:
                    key = self._make_key(ev)
                    self.existing[key] = ev
            except Exception:
                pass

    def _make_key(self, ev: Dict) -> str:
        nombre = (ev.get("nombre") or "").strip().lower()
        fecha = (ev.get("fecha") or "").strip()
        ciudad = (ev.get("ciudad") or "").strip().lower()
        return f"{nombre}|{fecha}|{ciudad}"

    def integrate(self, new_events: List[Dict]) -> List[Dict]:
        """Añade solo eventos nuevos (dedup por key). Devuelve lista final."""
        self.load_existing()
        trace = Trace("integrate", f"{len(new_events)} new")
        added = 0

        for ev in new_events:
            key = self._make_key(ev)
            if key in self.existing:
                # Merge: mantener el de mayor confidence
                if ev.get("confidence", 0) > self.existing[key].get("confidence", 0):
                    self.existing[key] = ev
                    trace.add("updated", key=key[:50])
            else:
                self.existing[key] = ev
                added += 1
                trace.add("added", key=key[:50])

        # Escribir atómicamente
        final = list(self.existing.values())
        tmp = self.output_file.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(final, f, ensure_ascii=False, indent=2)
        tmp.replace(self.output_file)

        trace.add("saved", total=len(final), added=added)
        return final


# --- Funciones de conveniencia ---

def run_ingest(url: str, use_tor: bool = True) -> Optional[str]:
    agent = IngestAgent(use_tor=use_tor)
    return agent.fetch(url)

async def run_ingest_pw(url: str, wait_selector: str = None, use_tor: bool = True) -> Optional[str]:
    agent = IngestAgent(use_tor=use_tor)
    return await agent.fetch_playwright(url, wait_selector)

def run_parse(html: str, url: str, source: str, target: str) -> List[Dict]:
    agent = ParseAgent()
    return agent.extract(html, url, source, target)

def run_qa(raw_events: List[Dict], source_html: str = "", min_conf: float = 0.6) -> List[Dict]:
    agent = QAAgent(min_confidence=min_conf)
    return agent.validate(raw_events, source_html)

def run_retry(url: str, html: str, domain: str, failed: str) -> Optional[str]:
    agent = RetryAgent()
    return agent.recover(url, html, domain, failed)

def run_integrate(new_events: List[Dict], output_file: str) -> List[Dict]:
    agent = IntegratorAgent(output_file)
    return agent.integrate(new_events)


if __name__ == "__main__":
    # Test rápido
    print("Agents module loaded")
    print("IngestAgent:", IngestAgent)
    print("ParseAgent:", ParseAgent)
    print("QAAgent:", QAAgent)
    print("RetryAgent:", RetryAgent)
    print("IntegratorAgent:", IntegratorAgent)