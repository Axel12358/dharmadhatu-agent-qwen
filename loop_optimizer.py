#!/usr/bin/env python3
"""
Loop de mejora continua para Dharmadhatu Bot v5.
Evalúa estrategias de scraping, ajusta parámetros automáticamente,
y guarda la configuración óptima para cada fuente.
"""

import json
import random
import time
import hashlib
import sys
from pathlib import Path
from datetime import datetime, timedelta

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

HISTORIAL_FILE = str(Path(_PROJECT_ROOT) / "historial_optimizacion.json")
MEJOR_CONFIG_FILE = str(Path(_PROJECT_ROOT) / "mejor_config.json")
SCORES_FILE = str(Path(_PROJECT_ROOT) / "estrategias_scores.json")


class EstrategiaScore:
    def __init__(self, nombre):
        self.nombre = nombre
        self.intentos = 0
        self.aciertos = 0
        self.total_eventos = 0
        self.tiempo_total = 0.0

    @property
    def tasa_exito(self):
        return self.aciertos / self.intentos if self.intentos > 0 else 0.0

    @property
    def eventos_por_intento(self):
        return self.total_eventos / self.intentos if self.intentos > 0 else 0.0

    @property
    def score(self):
        return self.total_eventos * 2 + self.aciertos * 5 - self.tiempo_total * 0.1


class LoopOptimizer:
    def __init__(self):
        self.historial = self._cargar_historial()
        self.estrategias = {
            "facebook_scraper": EstrategiaScore("facebook_scraper"),
            "playwright": EstrategiaScore("playwright"),
            "requests_direct": EstrategiaScore("requests_direct"),
            "grupos_conocidos": EstrategiaScore("grupos_conocidos"),
        }
        self._cargar_scores()

    def _cargar_historial(self):
        if Path(HISTORIAL_FILE).exists():
            try:
                with open(HISTORIAL_FILE) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _guardar_historial(self):
        try:
            with open(HISTORIAL_FILE, "w") as f:
                json.dump(self.historial[-100:], f, indent=2)
        except IOError:
            pass

    def _guardar_scores(self):
        try:
            data = {
                k: {
                    "intentos": v.intentos,
                    "aciertos": v.aciertos,
                    "total_eventos": v.total_eventos,
                    "tiempo_total": round(v.tiempo_total, 2),
                }
                for k, v in self.estrategias.items()
            }
            with open(SCORES_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except IOError:
            pass

    def _cargar_scores(self):
        scores_file = Path(SCORES_FILE)
        if scores_file.exists():
            try:
                with open(scores_file) as f:
                    data = json.load(f)
                for key, datos in data.items():
                    if key in self.estrategias:
                        e = self.estrategias[key]
                        e.intentos = datos.get("intentos", 0)
                        e.aciertos = datos.get("aciertos", 0)
                        e.total_eventos = datos.get("total_eventos", 0)
                        e.tiempo_total = datos.get("tiempo_total", 0.0)
            except (json.JSONDecodeError, IOError):
                pass

        for entry in self.historial:
            for key, data in entry.get("estrategias", {}).items():
                if key in self.estrategias:
                    e = self.estrategias[key]
                    e.intentos = data.get("intentos", 0)
                    e.aciertos = data.get("aciertos", 0)
                    e.total_eventos = data.get("total_eventos", 0)
                    e.tiempo_total = data.get("tiempo_total", 0.0)

    def registrar_resultado(self, estrategia, eventos, tiempo, exitoso=True):
        if estrategia in self.estrategias:
            e = self.estrategias[estrategia]
            e.intentos += 1
            e.total_eventos += len(eventos)
            e.tiempo_total += tiempo
            if exitoso and len(eventos) > 0:
                e.aciertos += 1
        self._guardar_scores()

    def obtener_orden_estrategias(self):
        """Devuelve estrategias ordenadas por score descendente."""
        return sorted(
            self.estrategias.values(),
            key=lambda e: e.score,
            reverse=True,
        )

    def mejor_estrategia(self):
        orden = self.obtener_orden_estrategias()
        return orden[0].nombre if orden else "grupos_conocidos"

    def guardar_iteracion(self, metricas, config_usada):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "metricas": metricas,
            "config": config_usada,
            "estrategias": {
                k: {
                    "intentos": v.intentos,
                    "aciertos": v.aciertos,
                    "total_eventos": v.total_eventos,
                    "tiempo_total": round(v.tiempo_total, 2),
                    "score": round(v.score, 2),
                }
                for k, v in self.estrategias.items()
            },
        }
        self.historial.append(entry)
        self._guardar_historial()
        self._guardar_mejor_config(config_usada, metricas)

    def _guardar_mejor_config(self, config, metricas):
        try:
            data = {
                "config": config,
                "metricas": metricas,
                "timestamp": datetime.now().isoformat(),
            }
            with open(MEJOR_CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except IOError:
            pass

    def generar_reporte(self):
        orden = self.obtener_orden_estrategias()
        reporte = [
            "=" * 50,
            "📊 REPORTE DEL LOOP DE MEJORA",
            "=" * 50,
        ]
        for i, e in enumerate(orden, 1):
            reporte.append(
                f"  {i}. {e.nombre:20s} | "
                f"eventos: {e.total_eventos:4d} | "
                f"tasa: {e.tasa_exito:.0%} | "
                f"score: {e.score:.1f}"
            )
        reporte.append(f"\n  🏆 Mejor estrategia: {self.mejor_estrategia()}")
        reporte.append(f"  📈 Total iteraciones: {len(self.historial)}")
        reporte.append("=" * 50)
        return "\n".join(reporte)

    def ajustar_parametros(self, config_base):
        """Ajusta parámetros según el historial de rendimiento."""
        config = dict(config_base)

        orden = self.obtener_orden_estrategias()
        if orden:
            mejor = orden[0].nombre
            if mejor == "facebook_scraper":
                config["priorizar_fb_scraper"] = True
                config["fb_pages"] = min(3, config.get("fb_pages", 1) + 1)
            elif mejor == "playwright":
                config["priorizar_playwright"] = True
                config["pw_timeout"] = min(30, config.get("pw_timeout", 10) + 5)
            elif mejor == "requests_direct":
                config["priorizar_requests"] = True
                config["req_timeout"] = min(30, config.get("req_timeout", 15) + 5)

        iteraciones = len(self.historial)
        if iteraciones >= 3:
            ultimas = self.historial[-3:]
            promedios = [h.get("metricas", {}).get("total_eventos", 0) for h in ultimas]
            if sum(promedios) / len(promedios) < 5:
                config["max_grupos"] = min(30, config.get("max_grupos", 10) + 5)
            else:
                config["max_grupos"] = max(10, config.get("max_grupos", 10) - 1)

        return config


_optimizer_instance = None


def get_optimizer():
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = LoopOptimizer()
    return _optimizer_instance


if __name__ == "__main__":
    opt = get_optimizer()
    print(opt.generar_reporte())
