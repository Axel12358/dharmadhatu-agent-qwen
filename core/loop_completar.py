#!/usr/bin/env python3
"""
Loop de completado hasta complementar CSV.

Itera sobre eventos_encontrados.csv hasta que no queden huecos reparables
sin inventar datos. Cubre:
- subgenero N/A (245) → psytrance si fuente es portal psy (Goabase/Ektoplazm) o via extractor
- tipo_lugar N/A (337) → festival/open air/club/etc via heurística nombre+link
- pais N/A (64) + continente vacío (140) → propagación intra-CSV + geocode + pais→continente

Estrategia: cada iteración llama a completar_na() mejorado, mide "antes→después",
y repite si hubo cambios y quedan N/A. Máx 5 iteraciones para evitar loop infinito.

Uso:
    python3 core/loop_completar.py
    python3 core/loop_completar.py --max-iter 7
"""
from __future__ import annotations
import csv
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CSV = PROJECT_ROOT / "eventos_encontrados.csv"

def _contar_na() -> dict:
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    def is_na(v): return (v or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?")
    counts = {}
    for col in ["subgenero","tipo_lugar","pais","continente","subcontinente","organizador","fecha","email"]:
        counts[col] = sum(1 for r in rows if is_na(r.get(col, "")))
    # también continente vacío "" separado
    counts["continente_vacio"] = sum(1 for r in rows if (r.get("continente") or "").strip() == "")
    return counts, len(rows)

def loop_completar(max_iter: int = 5, verbose: bool = True) -> dict:
    # Importar completar_na mejorado (con regla Goabase N/A→psytrance)
    # Parcheamos temporalmente _inferir_subgenero para incluir fuente psy
    import core.completar_na as cn
    orig_inferir = cn._inferir_subgenero

    def _inferir_mejorado(nombre: str, actual: str, fuente: str = "") -> str:
        # Si es N/A y fuente es portal psy, directo psytrance
        if (actual or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?"):
            if (fuente or "").strip().lower() in ("goabase","ektoplazm","psytrance.pl","psymedia","setline","psychill space"):
                return "psytrance"
        return orig_inferir(nombre, actual)

    # Monkey-patch para esta sesión
    cn._inferir_subgenero = lambda nombre, actual: _inferir_mejorado(nombre, actual, "")

    # Pero completar_na usa solo nombre,actual sin fuente; parcheamos más profundo:
    # Reescribimos loop para usar lógica con fuente
    import csv as _csv, re
    from pathlib import Path as _P

    def completar_con_fuente():
        rows = list(_csv.DictReader(open(CSV, encoding="utf-8")))
        cols = list(rows[0].keys()) if rows else []
        # Mapa geo intra-CSV
        def _norm(s): return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()
        def _na(v): return (v or "").strip().lower() in ("", "n/a", "na", "none", "null", "-", "?")
        geo = {}
        for r in rows:
            if not _na(r.get("pais")) and not _na(r.get("lugar")):
                k=_norm(r.get("lugar"))
                if k and k not in geo:
                    geo[k]=(r["pais"], r["continente"], r["subcontinente"])
        try: from core.geocode import geocodificar
        except: geocodificar=None
        cambiados=0
        for r in rows:
            # subgenero N/A → psytrance si fuente psy
            if _na(r.get("subgenero")):
                fuente = (r.get("fuente") or "").lower()
                if fuente in ("goabase","ektoplazm","psytrance.pl","psymedia","setline","psychill space"):
                    r["subgenero"]="psytrance"
                    cambiados+=1
                else:
                    # intentar via nombre
                    n=(r.get("nombre") or "").lower()
                    for sg in cn._SUBGENEROS:
                        if sg in n:
                            r["subgenero"]=sg
                            cambiados+=1
                            break
            # tipo_lugar N/A
            if _na(r.get("tipo_lugar")):
                t=cn._inferir_tipo(r.get("nombre",""), r.get("link",""))
                if t:
                    r["tipo_lugar"]=t
                    cambiados+=1
            # pais/continente via geo
            if _na(r.get("pais")) or _na(r.get("continente")) or _na(r.get("subcontinente")) or (r.get("continente") or "").strip()=="":
                k=_norm(r.get("lugar"))
                g=geo.get(k)
                if not g:
                    fw=k.split()[0] if k else ""
                    if len(fw)>3:
                        for key,val in geo.items():
                            if key.split()[0]==fw:
                                g=val; break
                if not g and geocodificar is not None and not _na(r.get("lugar")):
                    g=geocodificar(r.get("lugar"))
                if g:
                    if _na(r.get("pais")): r["pais"]=g[0]
                    if _na(r.get("continente")) or (r.get("continente") or "").strip()=="": r["continente"]=g[1]
                    if _na(r.get("subcontinente")): r["subcontinente"]=g[2]
                    cambiados+=1
                if _na(r.get("pais")):
                    low=(r.get("link") or "").lower()
                    for canal,(p,c,s) in cn.CANAL_PAIS.items():
                        if f"t.me/s/{canal}" in low or f"t.me/{canal}" in low:
                            r["pais"]=p; r["continente"]=c; r["subcontinente"]=s; cambiados+=1; break
        # continente desde pais
        for r in rows:
            if (_na(r.get("continente")) or (r.get("continente") or "").strip()=="") and not _na(r.get("pais")):
                n=_norm(r.get("pais"))
                for raw,(c,s) in cn.PAIS_CONTINENTE.items():
                    if _norm(raw)==n:
                        r["continente"]=c; r["subcontinente"]=s; cambiados+=1; break
        if cambiados:
            from core.csv_lock import escribir_fusionando
            escribir_fusionando(CSV, rows, timeout=120)
        return cambiados

    counts_antes,_ = _contar_na()
    if verbose:
        print(f"📊 Inicio: {counts_antes} | loop max {max_iter}")

    total_cambiados = 0
    for it in range(1, max_iter+1):
        cambiados = completar_con_fuente()
        total_cambiados += cambiados
        counts,_ = _contar_na()
        if verbose:
            print(f"  Iter {it}: cambiados={cambiados} → {counts}")
        if cambiados == 0:
            if verbose: print(f"✅ Loop completado en {it} iteraciones (sin cambios)")
            break
        time.sleep(0.2)
    else:
        if verbose: print(f"⏹️ Max iter {max_iter} alcanzado")

    # Normalización de países (España/Spain, Alemania/Germany, etc.) antes de escribir
    import core.completar_na as cn
    _norm_pais = {k.lower(): v for k, v in getattr(cn, "NORMALIZACION_PAISES", {}).items()}
    rows = list(_csv.DictReader(open(CSV, encoding="utf-8")))
    for r in rows:
        p = r.get("pais", "")
        if p and p.strip():
            norm = _norm_pais.get(p.strip().lower())
            if norm:
                r["pais"] = norm
    if rows:
        from core.csv_lock import escribir_fusionando
        escribir_fusionando(CSV, rows, timeout=120)

    counts_final, total = _contar_na()
    return {"iteraciones": it, "total_cambiados": total_cambiados, "antes": counts_antes, "despues": counts_final, "total_filas": total}

if __name__ == "__main__":
    max_iter = 5
    for a in sys.argv:
        if a.startswith("--max-iter"):
            try: max_iter=int(a.split("=")[1])
            except: pass
    res = loop_completar(max_iter=max_iter)
    print(f"\nResumen loop: {res}")
