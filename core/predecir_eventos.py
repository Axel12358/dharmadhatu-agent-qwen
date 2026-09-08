"""Predicción de ediciones futuras (2027) a partir del histórico del CSV.

Estrategia: los festivales psytrance suelen ser recurrentes (misma ventana de
mes, mismo lugar, mismo organizador). Agrupamos el histórico por
(nombre normalizado, lugar) y proyectamos la edición 2027 usando el mes más
frecuente de los años recientes (2025/2026). Cada predicción lleva un score de
confianza para que el usuario (o la Fase 2 de monitorización) filtre/promueva.

No se toca el CSV principal: se escribe un archivo aparte de candidatos.
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "eventos_encontrados.csv"
OUT_PATH = PROJECT_ROOT / "eventos_predichos_2027.csv"

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

_STOP = {"festival", "party", "rave", "event", "open", "air", "gathering",
         "fiesta", "night", "the", "de", "del", "y", "edition", "edicion"}


def _norm(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"20\d\d", "", s)              # quita años
    s = re.sub(r"\"\s*\d+\s*jahre\"", "", s)  # quita "25 jahre"
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_lugar(s: str) -> str:
    return _norm(s)


def _mes_de(fecha: str):
    """Devuelve (anio, mes) desde 'YYYY-MM-DD' o similar; None si no parsea."""
    if not fecha:
        return None
    m = re.search(r"(20\d\d)[-/.](\d{1,2})", fecha)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(20\d\d)", fecha)
    return (int(m.group(1)), 0) if m else None


def _confianza(n_recientes: int, n_years: int, meses: list[int]) -> float:
    """0..1. Prioriza serial anual (ocurrencias en >=2 años distintos);
    penaliza repeticiones dentro del mismo año (fiestas semanales/clubes)."""
    if n_recientes <= 0 or n_years <= 0:
        return 0.0
    if n_years >= 2:
        base = 0.85                      # señal fuerte de festival anual
    elif n_recientes >= 3:
        base = 0.55                      # recurrente misma temporada (sigue activo)
    elif n_recientes == 2:
        base = 0.45
    else:
        base = 0.30                      # una sola ocurrencia: especulativo
    if len(meses) >= 2:
        mode_count = Counter(meses).most_common(1)[0][1]
        cons_mes = mode_count / len(meses)
    else:
        cons_mes = 1.0
    base *= (0.7 + 0.3 * cons_mes)       # mes inestable => baja confianza
    return round(min(1.0, base), 2)


def _tipo(n_recientes: int, n_years: int) -> str:
    if n_years >= 2:
        return "anual"
    if n_recientes >= 2:
        return "recurrente_mismo_ano"
    return "unica_ocurrencia"


def predecir_eventos_2027(csv_path: Path = CSV_PATH,
                          out_path: Path = OUT_PATH,
                          umbral: float = 0.0) -> list[dict]:
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    grupos = defaultdict(list)
    for r in rows:
        am = _mes_de(r.get("fecha", ""))
        if not am:
            continue
        anio, mes = am
        if anio < 2024:        # descarta ruido de parseos viejos
            continue
        key = (_norm(r.get("nombre", "")), _norm_lugar(r.get("lugar", "")))
        if not key[0]:
            continue
        grupos[key].append({
            "anio": anio, "mes": mes,
            "lugar": r.get("lugar", ""), "pais": r.get("pais", ""),
            "continente": r.get("continente", ""), "subcontinente": r.get("subcontinente", ""),
            "organizador": r.get("organizador", ""), "subgenero": r.get("subgenero", ""),
            "tipo_lugar": r.get("tipo_lugar", ""), "link": r.get("link", ""),
            "nombre_raw": r.get("nombre", ""),
        })

    predichos: list[dict] = []
    for (base, lugar_n), evs in grupos.items():
        recientes = [e for e in evs if e["anio"] in (2025, 2026)]
        if not recientes:
            continue
        n_years = len({e["anio"] for e in recientes})
        meses = [e["mes"] for e in recientes if e["mes"]]
        if not meses:
            meses = [e["mes"] for e in evs if e["mes"]]
        mes_mode = Counter(meses).most_common(1)[0][0] if meses else 0
        def _mas_comun(campo):
            vals = [e[campo] for e in recientes if e[campo]]
            return Counter(vals).most_common(1)[0][0] if vals else ""
        conf = _confianza(len(recientes), n_years, meses)
        tipo = _tipo(len(recientes), n_years)
        if conf < umbral:
            continue
        nombre_base = base.strip()
        predichos.append({
            "nombre_predicho": f"{nombre_base.title()} 2027",
            "fecha_estimada": f"2027-{mes_mode:02d}" if mes_mode else "2027",
            "mes": _MESES[mes_mode - 1] if mes_mode else "",
            "lugar": _mas_comun("lugar"),
            "pais": _mas_comun("pais"),
            "continente": _mas_comun("continente"),
            "subcontinente": _mas_comun("subcontinente"),
            "organizador": _mas_comun("organizador"),
            "subgenero": _mas_comun("subgenero"),
            "tipo_lugar": _mas_comun("tipo_lugar"),
            "confianza": conf,
            "ocurrencias": len(recientes),
            "tipo": tipo,
            "fuente": "prediccion_recurrente",
            "link_organizador": _mas_comun("link"),
            "base": nombre_base,
        })

    predichos.sort(key=lambda d: d["confianza"], reverse=True)
    campos = ["nombre_predicho", "fecha_estimada", "mes", "lugar", "pais",
              "continente", "subcontinente", "organizador", "subgenero",
              "tipo_lugar", "confianza", "ocurrencias", "tipo", "fuente",
              "link_organizador", "base"]
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(predichos)
    return predichos


if __name__ == "__main__":
    res = predecir_eventos_2027()
    print(f"Predicciones 2027 generadas: {len(res)}")
    print("Por tipo:", dict(Counter(p["tipo"] for p in res)))
    print("Por confianza (>=0.7):", sum(1 for p in res if p["confianza"] >= 0.7))
    print("Top anuales (confianza alta):")
    for p in [x for x in res if x["tipo"] == "anual"][:15]:
        print(f"  {p['confianza']:.2f}  {p['nombre_predicho'][:32]:32} "
              f"{p['fecha_estimada']}  {p['lugar'][:18]}  ({p['subgenero'][:12]})")
