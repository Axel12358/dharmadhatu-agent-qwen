#!/usr/bin/env python3
"""
Dharmadhatu Bot v5 - Loop de mejora continua.

Prueba combinaciones de configuración (lote de ciudades RA, horizonte RA,
keywords FB y visitas de enriquecimiento FB), ejecuta el bot para cada una,
evalúa con un score y persiste la mejor configuración en
`mejor_configuracion.json`. Genera un reporte markdown (`reporte_loop.md`).

Score = (eventos_totales * peso_eventos)
      + (eventos_con_email * peso_email)
      - (tiempo_ejecucion * penalizacion_tiempo)

Reglas del proyecto:
- NO toca goabase.py, songkick.py ni main.py (solo los INVOCA como módulos).
- Siempre suma, nunca resta: solo amplía límites y añade ciudades.
- Todo local y gratuito, sin login.
- Reversible: las fuentes estáticas (Goabase/Songkick/Instagram) se cachean
  en memoria y los límites se restablecen tras cada iteración.

Ejecución:
    python3 loop_mejora.py            # usa config_loop.json
    python3 loop_mejora.py --reset    # borra historial y mejores anteriores
"""

import asyncio
import itertools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.helpers import save_csv

CONFIG_LOOP_FILE = Path(_PROJECT_ROOT) / "config_loop.json"
MEJOR_CONFIG_FILE = Path(_PROJECT_ROOT) / "mejor_configuracion.json"
HISTORIAL_FILE = Path(_PROJECT_ROOT) / "historial_loop.json"


def _load_json(path, default=None):
    if Path(path).exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return default


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _generar_combinaciones(cfg):
    """Producto cartesiano de las dimensiones a probar (lotes RA × keywords
    FB × visitas FB × horizonte RA × max eventos). Ordenado para variar primero
    la dimensión más barata (ciudades RA) y dejar FB para después."""
    ra = cfg.get("ra", {})
    fb = cfg.get("facebook", {})
    lotes = ra.get("lotes_ciudades", [16])
    horizontes = ra.get("horizonte_meses", [None])
    max_ev = ra.get("max_eventos", [120])
    kws = fb.get("max_keywords", [250])
    visitas = fb.get("max_visitas_enriquecimiento", [30])

    combos = []
    for h, mev in itertools.product(horizontes, max_ev):
        for lote in lotes:
            for kw, vis in itertools.product(kws, visitas):
                combos.append({
                    "lote_ciudades": lote,
                    "horizonte_meses": h,
                    "max_eventos": mev,
                    "max_keywords": kw,
                    "max_visitas": vis,
                })
    return combos


def _contar_por_fuente(eventos):
    por_fuente = {}
    for ev in eventos:
        f = ev.get("fuente") or "Desconocida"
        por_fuente[f] = por_fuente.get(f, 0) + 1
    return por_fuente


def _dedupe(eventos):
    """Dedupe cruzado aproximado por (nombre, fecha, lugar) para el reporte."""
    vistos = set()
    unicos = []
    for ev in eventos:
        k = (str(ev.get("nombre", "")).lower(),
             str(ev.get("fecha", "")),
             str(ev.get("lugar", "")).lower())
        if k in vistos:
            continue
        vistos.add(k)
        unicos.append(ev)
    return unicos


async def _fuentes_estaticas(incluir):
    """Goabase, Songkick e Instagram no dependen de la configuración del loop:
    se ejecutan una sola vez y se reutilizan en cada iteración."""
    estaticas = []
    if incluir.get("goabase", True):
        try:
            from scrapers.goabase import scrape_goabase
            evs = await asyncio.to_thread(scrape_goabase, limit=100)
            print(f"   Goabase: {len(evs or [])} eventos (estáticos)")
            estaticas.extend(evs or [])
        except Exception as e:
            print(f"   Goabase: error (flujo continúa): {e}")
    if incluir.get("songkick", True):
        try:
            from scrapers.songkick import scrape_songkick
            evs = await asyncio.to_thread(scrape_songkick, limit=100)
            print(f"   Songkick: {len(evs or [])} eventos (estáticos)")
            estaticas.extend(evs or [])
        except Exception as e:
            print(f"   Songkick: error (flujo continúa): {e}")
    if incluir.get("instagram", True):
        try:
            from scrapers.instagram import scrape_instagram_events
            evs = await asyncio.to_thread(scrape_instagram_events)
            print(f"   Instagram: {len(evs or [])} eventos (estáticos)")
            estaticas.extend(evs or [])
        except ImportError:
            print("   Instagram: módulo no disponible (instaloader ausente) — omitido")
        except Exception as e:
            print(f"   Instagram: error (flujo continúa): {e}")
    return estaticas


async def _ejecutar_iteracion(combo, ciudades_lista, estaticas, incluir, pesos):
    from scrapers.resident_advisor import scrape_ra, set_parametros_loop
    from scrapers.facebook_mcp import scrape_facebook_events

    n = combo["lote_ciudades"]
    subset = ciudades_lista[:n]

    # Configura la iteración (límites temporales, reversibles)
    set_parametros_loop(
        ciudades=subset,
        max_eventos=combo["max_eventos"],
        horizonte_meses=combo["horizonte_meses"],
    )

    t0 = time.time()
    eventos = list(estaticas)

    if incluir.get("resident_advisor", True):
        evs = await asyncio.to_thread(scrape_ra)
        print(f"   RA: {len(evs or [])} eventos ({n} ciudades)")
        eventos.extend(evs or [])

    if incluir.get("facebook", True):
        evs = await scrape_facebook_events(
            max_keywords=combo["max_keywords"],
            max_visitas=combo["max_visitas"],
        )
        print(f"   Facebook: {len(evs or [])} eventos")
        eventos.extend(evs or [])

    duracion = time.time() - t0

    # Restablecer límites (reversible)
    set_parametros_loop(ciudades=None, max_eventos=None, horizonte_meses=None)

    total = len(eventos)
    con_email = sum(1 for ev in eventos
                    if ev.get("email") and str(ev.get("email")) not in ("N/A", ""))
    score = (total * pesos.get("eventos", 2)
             + con_email * pesos.get("email", 3)
             - duracion * pesos.get("tiempo", 0.1))

    return {
        "configuracion": combo,
        "eventos_totales": total,
        "eventos_deduplicados": len(_dedupe(eventos)),
        "eventos_con_email": con_email,
        "tiempo_segundos": round(duracion, 1),
        "por_fuente": _contar_por_fuente(eventos),
        "score": round(score, 2),
    }


def _guardar_mejor(mejor, cfg):
    """Persiste la mejor configuración (reversible: guarda un historial)."""
    payload = {
        "timestamp": datetime.now().isoformat(),
        "configuracion": mejor["configuracion"],
        "score": mejor["score"],
        "eventos_totales": mejor["eventos_totales"],
        "eventos_con_email": mejor["eventos_con_email"],
        "tiempo_segundos": mejor["tiempo_segundos"],
        "por_fuente": mejor["por_fuente"],
        "pesos_score": cfg.get("pesos_score", {}),
    }
    _save_json(MEJOR_CONFIG_FILE, payload)

    # Historial reversible (mantener configuraciones anteriores)
    historial = _load_json(HISTORIAL_FILE, {"ejecuciones": [], "mejores_previos": []})
    historial["mejores_previos"] = historial.get("mejores_previos", [])
    historial["mejores_previos"].append(payload)
    _save_json(HISTORIAL_FILE, historial)


def _recomendacion(best_res, cfg):
    """Sugerencia para la próxima iteración basada en la mejor config."""
    rec = []
    conf = best_res["configuracion"]
    lotes = cfg.get("ra", {}).get("lotes_ciudades", [])
    kws = cfg.get("facebook", {}).get("max_keywords", [])
    vis = cfg.get("facebook", {}).get("max_visitas_enriquecimiento", [])
    if lotes and conf["lote_ciudades"] >= max(lotes):
        rec.append("Probar con más ciudades de RA (subir el lote o añadir nuevas).")
    if kws and conf["max_keywords"] >= max(kws):
        rec.append("Probar con más keywords de Facebook (>400).")
    if vis and conf["max_visitas"] >= max(vis):
        rec.append("Probar con más visitas de enriquecimiento (>50).")
    if not rec:
        rec.append("Explorar las combinaciones restantes de config_loop.json.")
    return " ".join(rec)


def _reporte_markdown(resultados, mejor, cfg, recomendacion):
    lineas = []
    lineas.append("# Reporte del loop de mejora continua")
    lineas.append("")
    lineas.append(f"- Fecha: {datetime.now().isoformat()}")
    lineas.append(f"- Iteraciones ejecutadas: {len(resultados)}")
    if mejor:
        lineas.append(f"- **Mejor score**: {mejor['score']}")
        lineas.append(f"  - Eventos totales: {mejor['eventos_totales']} "
                      f"({mejor['eventos_deduplicados']} deduplicados)")
        lineas.append(f"  - Eventos con email: {mejor['eventos_con_email']}")
        lineas.append(f"  - Tiempo: {mejor['tiempo_segundos']} s")
        lineas.append(f"  - Config: {json.dumps(mejor['configuracion'], ensure_ascii=False)}")
        lineas.append(f"  - Por fuente: {json.dumps(mejor['por_fuente'], ensure_ascii=False)}")
    lineas.append("")
    lineas.append("## Tabla de iteraciones")
    lineas.append("")
    lineas.append("| # | Ciudades RA | Horizonte (m) | Keywords FB | Visitas FB | "
                  "Eventos | Con email | Tiempo (s) | Score |")
    lineas.append("|---|-------------|---------------|-------------|------------|"
                  "---------|-----------|------------|-------|")
    for i, r in enumerate(resultados, 1):
        c = r["configuracion"]
        lineas.append(
            f"| {i} | {c['lote_ciudades']} | {c['horizonte_meses']} | "
            f"{c['max_keywords']} | {c['max_visitas']} | {r['eventos_totales']} | "
            f"{r['eventos_con_email']} | {r['tiempo_segundos']} | {r['score']} |"
        )
    lineas.append("")
    lineas.append("## Recomendación")
    lineas.append("")
    lineas.append(recomendacion)
    lineas.append("")
    return "\n".join(lineas)


async def main():
    cfg = _load_json(CONFIG_LOOP_FILE, {})
    if not cfg:
        print("❌ No se encontró config_loop.json")
        return

    if "--reset" in sys.argv:
        for f in (MEJOR_CONFIG_FILE, HISTORIAL_FILE):
            if Path(f).exists():
                Path(f).unlink()
        print("🧹 Historial y mejores configuraciones borrados.")

    ciudades_lista = cfg.get("ra", {}).get("ciudades") or []
    if not ciudades_lista:
        from scrapers.resident_advisor import CIUDADES_RA
        ciudades_lista = list(CIUDADES_RA)

    pesos = cfg.get("pesos_score", {})
    incluir = cfg.get("incluir_fuentes", {})
    max_iter = int(cfg.get("max_iteraciones", 12))
    reusar = cfg.get("reusar_estaticas", True)

    combos = _generar_combinaciones(cfg)
    if len(combos) > max_iter:
        print(f"🔢 {len(combos)} combinaciones posibles → probando {max_iter} "
              f"(ajusta max_iteraciones en config_loop.json para más).")
        combos = combos[:max_iter]
    else:
        print(f"🔢 Probando {len(combos)} combinaciones.")

    estaticas = []
    if reusar:
        print("📦 Cargando fuentes estáticas (Goabase/Songkick/Instagram)...")
        estaticas = await _fuentes_estaticas(incluir)

    print("=" * 60)
    mejores_previos = _load_json(HISTORIAL_FILE, {}).get("mejores_previos", [])
    if mejores_previos:
        print(f"🕒 Mejores configuraciones previas: {len(mejores_previos)} (historial reversible)")

    mejor = None
    resultados = []
    try:
        for i, combo in enumerate(combos, 1):
            print(f"\n▶️ Iteración {i}/{len(combos)}: {json.dumps(combo)}")
            res = await _ejecutar_iteracion(combo, ciudades_lista, estaticas, incluir, pesos)
            resultados.append(res)
            print(f"   ✅ Score {res['score']} | eventos {res['eventos_totales']} "
                  f"(email {res['eventos_con_email']}) | {res['tiempo_segundos']} s")
            if mejor is None or res["score"] > mejor["score"]:
                mejor = res
                _guardar_mejor(mejor, cfg)
                print(f"   ⭐ Nueva mejor configuración → mejor_configuracion.json")
    except KeyboardInterrupt:
        print("\n⏹️ Loop interrumpido por el usuario. Guardando resultados parciales.")
    finally:
        # Reversible: restablece cualquier límite residual
        try:
            from scrapers.resident_advisor import set_parametros_loop
            from scrapers.facebook_mcp import set_limites_loop
            set_parametros_loop(ciudades=None, max_eventos=None, horizonte_meses=None)
            set_limites_loop(max_keywords=None, max_visitas=None)
        except Exception:
            pass

    if not resultados:
        print("❌ Sin resultados.")
        return

    # Guardar reporte de la ejecución actual
    historial = _load_json(HISTORIAL_FILE, {"ejecuciones": [], "mejores_previos": []})
    historial.setdefault("ejecuciones", []).append({
        "timestamp": datetime.now().isoformat(),
        "resultados": resultados,
    })
    _save_json(HISTORIAL_FILE, historial)

    recomendacion = _recomendacion(mejor, cfg) if mejor else "Sin mejor config."
    reporte = cfg.get("reporte", "reporte_loop.md")
    with open(Path(_PROJECT_ROOT) / reporte, "w", encoding="utf-8") as f:
        f.write(_reporte_markdown(resultados, mejor, cfg, recomendacion))
    print(f"\n📄 Reporte guardado: {reporte}")

    # CSV del mejor resultado (re-ejecutando fuentes para materializarlo)
    if mejor and incluir.get("resident_advisor", True):
        try:
            from scrapers.resident_advisor import scrape_ra, set_parametros_loop
            from scrapers.facebook_mcp import scrape_facebook_events
            n = mejor["configuracion"]["lote_ciudades"]
            set_parametros_loop(ciudades=ciudades_lista[:n],
                                max_eventos=mejor["configuracion"]["max_eventos"],
                                horizonte_meses=mejor["configuracion"]["horizonte_meses"])
            print("\n📊 Generando CSV con la mejor configuración...")
            eventos_final = list(estaticas)
            evs = await asyncio.to_thread(scrape_ra)
            eventos_final.extend(evs or [])
            set_parametros_loop(ciudades=None, max_eventos=None, horizonte_meses=None)
            if incluir.get("facebook", True):
                try:
                    evs = await scrape_facebook_events(
                        max_keywords=mejor["configuracion"]["max_keywords"],
                        max_visitas=mejor["configuracion"]["max_visitas"],
                    )
                    eventos_final.extend(evs or [])
                except Exception as e:
                    print(f"   ⚠️ Facebook en CSV: error (flujo continúa): {e}")
            save_csv(eventos_final, cfg.get("csv", "eventos_encontrados.csv"))
        except Exception as e:
            print(f"   ⚠️ Error generando CSV: {e}")

    print("\n" + "=" * 60)
    print("📊 RESUMEN DEL LOOP")
    print("=" * 60)
    for i, r in enumerate(resultados, 1):
        c = r["configuracion"]
        print(f"  {i:>2}. ciudades={c['lote_ciudades']:>2} kw={c['max_keywords']:>3} "
              f"vis={c['max_visitas']:>2} → {r['eventos_totales']:>3} eventos "
              f"(email {r['eventos_con_email']:>2}) {r['tiempo_segundos']:>6.1f}s "
              f"score {r['score']}")
    if mejor:
        print(f"\n🏆 Mejor configuración: {json.dumps(mejor['configuracion'], ensure_ascii=False)}")
        print(f"   Score {mejor['score']} · {mejor['eventos_totales']} eventos · "
              f"{mejor['eventos_con_email']} con email")
        print(f"   Guardada en {MEJOR_CONFIG_FILE.name}")
    print(f"\n💡 Recomendación: {recomendacion}")


if __name__ == "__main__":
    asyncio.run(main())
