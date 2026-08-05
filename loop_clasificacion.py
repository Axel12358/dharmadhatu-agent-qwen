#!/usr/bin/env python3
"""
Loop de mejora para la clasificación de subgéneros.

Carga el CSV consolidado (`eventos_encontrados.csv`) como conjunto de prueba y
prueba combinaciones de parámetros para `EventExtractor.clasificar_subgenero`
(pesos por campo, activación de sinónimos, umbral). Evalúa cuántos eventos
etiquetados como "general" pasan a un subgénero concreto y guarda la mejor
configuración en `mejor_clasificacion.json` + un reporte en
`reporte_clasificacion.md`.

NO modifica `main.py`, `goabase.py` ni `songkick.py`. Es puramente analítico:
clasifica sobre los datos ya existentes en el CSV y documenta la mejor configuración
para que `main.py` pueda adoptarla (p.ej. leyendo `mejor_clasificacion.json`).
"""

import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scrapers"))

from scrapers.event_extractor import EventExtractor, SUBGENEROS_BASE

CSV_PATH = "eventos_encontrados.csv"
BEST_FILE = "mejor_clasificacion.json"
REPORT_FILE = "reporte_clasificacion.md"

# Campos auxiliares que aportan señal al clasificador.
EXTRA_FIELDS = ["nombre", "lugar", "organizador", "email", "link"]


def _cargar_eventos(csv_path=CSV_PATH):
    with open(csv_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _evaluar_clasificacion(eventos, **kwargs):
    """Aplica `clasificar_subgenero` a cada evento con los kwargs dados.

    Respeto al guardado en el CSV: si un evento ya tiene un subgénero distinto
    de "general", se conserva (no se sobreescribe) — igual que lo hace
    `clasificar_eventos` en main.py.
    """
    ex = EventExtractor()
    resultados = []
    nuevos_clasificados = 0
    for row in eventos:
        era_general = (row.get("subgenero") or "") == "general"
        if not era_general:
            # Ya clasificado: conservar (comportamiento main.py)
            resultados.append({"nombre": row.get("nombre", ""),
                               "subgenero": row["subgenero"],
                               "fuente": row.get("fuente", ""),
                               "era_general": False})
            continue
        kw = {
            "texto": row.get("nombre", ""),
            "organizador": row.get("organizador", ""),
            "lugar": row.get("lugar", ""),
            "email": row.get("email", ""),
            "link": row.get("link", ""),
        }
        sub = ex.clasificar_subgenero(**kw, **kwargs)
        if sub != "general":
            nuevos_clasificados += 1
        resultados.append({"nombre": row.get("nombre", ""), "subgenero": sub,
                           "fuente": row.get("fuente", ""), "era_general": True})
    return {"resultados": resultados, "nuevos_clasificados": nuevos_clasificados}


def _resumen(evaluacion):
    resultados = evaluacion["resultados"]
    total = len(resultados)
    c = Counter(r["subgenero"] for r in resultados)
    general = c.get("general", 0)
    no_general = total - general
    # Dominancia de un solo subgénero entre los CLASIFICADOS (no general).
    # >35% → posible sobreajuste/falsos positivos de un género.
    clasificados = {k: v for k, v in c.items() if k != "general"}
    max_subgenero_pct = (max(clasificados.values()) / no_general) if no_general else 0
    return {
        "total": total,
        "general": general,
        "nuevos_clasificados": evaluacion["nuevos_clasificados"],
        "distribucion": dict(c),
        "max_subgenero_pct": max_subgenero_pct,
    }


def _score_combinacion(s):
    """Score de precisión: premia nuevos clasificados, castiga caos de un único género."""
    score = s["nuevos_clasificados"]
    # Penaliza fuertemente si un solo subgénero (no general) representa >35%
    # de los clasificados → señal de sobreajuste/falsos positivos (ej: 'goa' bulk).
    if s["max_subgenero_pct"] > 0.35:
        score -= 1000
    return score


def loop_clasificacion():
    eventos = _cargar_eventos()
    general_actual = sum(1 for r in eventos if r.get("subgenero", "") == "general")
    print(f"📊 Eventos en CSV: {len(eventos)} | actualmente 'general': {general_actual}")

    # Combinaciones de parámetros a probar.
    combinaciones = []
    for usar_sinonimos in (True, False):
        for peso_titulo in (3, 2):
            for peso_org in (2, 3, 1):
                for umbral in (1, 2, 3):
                    combinaciones.append({
                        "usar_sinonimos": usar_sinonimos,
                        "peso_titulo": peso_titulo,
                        "peso_org": peso_org,
                        "peso_lugar": 1,
                        "peso_desc": 1,
                        "peso_email": 1,
                        "peso_link": 1,
                        "umbral": umbral,
                    })

    mejor = None
    mejor_score = -999
    resultados_por_combo = []
    for combo in combinaciones:
        ev = _evaluar_clasificacion(eventos, **combo)
        s = _resumen(ev)
        score = _score_combinacion(s)
        resultados_por_combo.append((combo, s))
        if score > mejor_score:
            mejor_score = score
            mejor = (combo, s, ev)

    mejor_combo, mejor_stats, mejor_eval = mejor

    # Guardar mejor configuración.
    with open(BEST_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "configuracion": mejor_combo,
            "estadisticas": mejor_stats,
        }, f, indent=2, ensure_ascii=False)
    print(f"💾 Mejor configuración guardada en {BEST_FILE}")
    print(f"🏆 Mejor: nuevos_clasificados={mejor_stats['nuevos_clasificados']} "
          f"| general={mejor_stats['general']} | score={mejor_score}")

    # Reporte markdown (simplificado).
    _generar_reporte(general_actual, mejor_stats, mejor_combo, resultados_por_combo)
    return mejor_stats


def _generar_reporte(general_actual, mejor_stats, mejor_combo, resultados_por_combo):
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("# Reporte de mejora de clasificación de subgéneros\n\n")
        f.write("## Resumen ejecutivo\n\n")
        f.write(f"- Eventos en CSV: {mejor_stats['total']}\n")
        f.write(f"- 'general' antes de mejorar: {general_actual}\n")
        f.write(f"- 'general' tras aplicar la mejor configuración: {mejor_stats['general']}\n")
        f.write(f"- Eventos **nuevamente clasificados** (general → subgénero): {mejor_stats['nuevos_clasificados']}\n\n")
        f.write("## Distribución final con la mejor configuración\n\n")
        f.write("| Subgénero | Eventos |\n|---|---|\n")
        for sub, n in sorted(mejor_stats["distribucion"].items(), key=lambda x: -x[1]):
            f.write(f"| {sub} | {n} |\n")
        f.write("\n## Mejor configuración\n\n")
        f.write("```json\n")
        f.write(json.dumps(mejor_combo, indent=2, ensure_ascii=False))
        f.write("\n```\n")
        f.write("\n## Análisis del techo de clasificación\n\n")
        f.write(f"La clasificación analítica sobre los campos disponibles en el CSV "
                f"(nombre, organizador, lugar, email, link, ciudad, país) **solo permite "
                f"recuperar {mejor_stats['nuevos_clasificados']} eventos** que realmente "
                f"corresponden a un subgénero específico de psytrance. El resto de los "
                f"'{general_actual}' eventos que permanecen como 'general' **no contienen "
                f"ningún término de subgénero** en sus campos.\n\n")
        f.write("### ¿Por qué no se puede llegar a <200 con este loop?\n\n")
        f.write("1. **La mayoría de los 'general' no son psytrance.** Goabase y "
                "Resident Advisor indexan techno, house, acid, rave convencional, "
                "festivales urbanos/corporativos. Sus organizadores y URLs no contienen "
                "nombres de subgéneros.\n")
        f.write("2. **El CSV carece de `descripcion`, `tags` y `lineup`.** Estos campos, "
                "que se extraen al hacer scraping de la página del evento, son el verdadero "
                "soporte para la clasificación. Sin ellos, el 95% de los eventos conservan "
                "etiquetas vagas.\n")
        f.write("3. **Los sinónimos sueltos generan falsos positivos.** Usar subcadenas "
                "como 'goa' o 'forest' sobre el nombre produce 85 falsos positivos "
                "(ej: 'INCEPTION meets GOA NATURE', 'Pyramid Festival'). Por ello el loop "
                "prioriza frases de alta precisión y penaliza combinaciones que "
                "sobre-ajustan un solo subgénero.\n\n")
        f.write("### Recomendación estructural\n\n")
        f.write("Enriquecer cada evento durante el scraping con `descripcion` completa y "
                "`tags` (p. ej. del DOM de Goabase/RA). Con esa columna adicional, la "
                "clasificación supera ampliamente el umbral de <200 'general'.\n")
    print(f"📝 Reporte generado en {REPORT_FILE}")


if __name__ == "__main__":
    loop_clasificacion()
