#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Capa de supervisión y mejora basada en álgebra lineal (Dharmadhatu Bot v5).

Autocontenido y OPTATIVO: solo usa `numpy` (ya instalado). Si este módulo (o
las dependencias) no está disponible, el bot continúa funcionando igual; todas
las integraciones externas van bajo `try/except ImportError`.

Aplica álgebra lineal para mejorar la calidad de `eventos_encontrados.csv` sin
intervención humana:
  1. TF-IDF : representa cada evento como vector (nombres/descripciones).
  2. Deduplicación semántica : similitud coseno + fechas iguales (o +/-1 día).
  3. Predicción de subgénero  : regresión logística (softmax) sobre TF-IDF.
  4. Predicción de tipo de lugar (indoor/outdoor) : reglas + clasificador.
  5. Supervisión global      : `supervisar_y_mejorar()` rellena N/A con
     confianza alta y genera `mejoras_algebra.json`.
  6. Entrenamiento automático: la primera ejecución entrena y guarda modelos;
     reentrena periódicamente para incorporar nuevos datos etiquetados.

Modelos persistentes en `models/`:
  - tfidf_vectorizer.pkl
  - subgenero_classifier.pkl
  - tipo_lugar_classifier.pkl

Siempre aditivo y reversible: las predicciones se guardan solo si superan un
umbral de confianza; nunca sobrescribe datos existentes válidos salvo para
completar "N/A".
"""

from datetime import date, datetime
import json
import os
import pickle
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Rutas y configuración
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

TFIDF_PATH = MODELS_DIR / "tfidf_vectorizer.pkl"
SUBGENERO_PATH = MODELS_DIR / "subgenero_classifier.pkl"
TIPO_LUGAR_PATH = MODELS_DIR / "tipo_lugar_classifier.pkl"
CONTADOR_PATH = MODELS_DIR / "entrenamiento_counter.txt"
REPORTE_PATH = PROJECT_ROOT / "mejoras_algebra.json"

# Reentrenar modelos cada N ejecuciones para incorporar nuevos datos.
RETRAIN_CADA = 10
# Umbral de similitud para considerar duplicado (historia cercana).
UMBRAL_SIMILITUD = 0.85
# Confianza mínima de probabilidad para aceptar una predicción.
UMBRAL_CONFIANZA = 0.6
# Máximo de features (palabras) del vocabulario.
MAX_FEATURAS = 8000
# Campos de texto que representan a un evento para vectorizar.
CAMPOS_TEXTO = ("nombre", "lugar", "organizador", "fuente")
# Subgéneros que no son psytrance (no se usan para predecir subgénero).
SUBGENEROS_NO_PSY = {"no_psy", "general", "n/a", "na", ""}
# Umbral de similitud para deduplicar usando SÓLO del nombre (conservador,
# para no fusionar eventos distintos del mismo recinto/ciudad).
UMBRAL_NOMBRE = 0.92


# ---------------------------------------------------------------------------
# Utilidades de texto y fechas
# ---------------------------------------------------------------------------
def _limpiar(texto: Any) -> str:
    return " ".join(str(texto or "").split()).lower()


def _tokenizar(texto: Any) -> List[str]:
    """Minúsculas, sin acentos, solo palabras alfanuméricas."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = t.encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", t)


_MESES = {
    "jan": 1, "january": 1, "ene": 1, "enero": 1,
    "feb": 2, "february": 2, "febrero": 2,
    "mar": 3, "march": 3, "marzo": 3,
    "apr": 4, "april": 4, "abr": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "jun": 6, "june": 6, "junio": 6,
    "jul": 7, "july": 7, "julio": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9,
    "oct": 10, "october": 10, "octubre": 10,
    "nov": 11, "november": 11, "noviembre": 11,
    "dec": 12, "december": 12, "dic": 12, "diciembre": 12,
}


def _mes_numero(nombre: str) -> int:
    nombre = _limpiar(nombre)
    if nombre in _MESES:
        return _MESES[nombre]
    raise KeyError(nombre)


def _fecha_evento(valor: Any) -> Optional[date]:
    """Parsea una fecha del CSV a `datetime.date`; None si no se puede."""
    v = _limpiar(valor)
    if not v or v in ("n/a", "na", "tba", "nan"):
        return None
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", v)
    if m:
        try:
            return date(int(m.group(3)), _mes_numero(m.group(2)), int(m.group(1)))
        except (ValueError, KeyError):
            return None
    m = re.match(r"([a-z]+)\s+(\d{1,2}),?\s*(\d{4})?", v)
    if m:
        try:
            return date(int(m.group(3) or datetime.now().year),
                        _mes_numero(m.group(1)), int(m.group(2)))
        except (ValueError, KeyError):
            return None
    return None


def _mismas_fechas(a: Any, b: Any, tol_dias: int = 1) -> bool:
    """True si ambas fechas son iguales o difieren en <= tol_dias.

    Si alguna no es parseable, devuelve False (no se eliminan por error).
    """
    da = _fecha_evento(a)
    db = _fecha_evento(b)
    if da is None or db is None:
        return False
    return abs((da - db).days) <= tol_dias


# ---------------------------------------------------------------------------
# 1) Vectorizador TF-IDF
# ---------------------------------------------------------------------------
class VectorizadorTFIDF:
    """TF-IDF sencillo sobre numpy: vocabulario, df, idf, matriz L2."""

    def __init__(self, max_features: int = MAX_FEATURAS):
        self.max_features = max_features
        self.vocab: List[str] = []
        self.idf_: Optional[np.ndarray] = None
        self._indice: Dict[str, int] = {}

    def fit(self, textos: Sequence[Any]) -> "VectorizadorTFIDF":
        from collections import Counter
        doc_freq: Counter = Counter()
        n_docs = 0
        for t in textos:
            toks = _tokenizar(t)
            if not toks:
                continue
            n_docs += 1
            doc_freq.update(set(toks))
        if n_docs == 0:
            self.vocab = []
            self.idf_ = np.array([], dtype=float)
            self._indice = {}
            return self
        palabras = [w for w, _ in doc_freq.most_common(self.max_features)]
        self.vocab = palabras
        self._indice = {w: i for i, w in enumerate(palabras)}
        freqs = np.array([doc_freq[w] for w in palabras], dtype=float)
        self.idf_ = np.log((n_docs + 1) / (freqs + 1)) + 1.0
        return self

    def transform(self, textos: Sequence[Any]) -> np.ndarray:
        if not self.vocab:
            return np.zeros((len(textos), 0), dtype=float)
        filas = []
        for t in textos:
            fila = np.zeros(len(self.vocab), dtype=float)
            for w in _tokenizar(t):
                i = self._indice.get(w)
                if i is not None:
                    fila[i] += self.idf_[i]
            n = np.linalg.norm(fila)
            if n > 0:
                fila = fila / n
            filas.append(fila)
        return np.vstack(filas) if filas else np.zeros((0, len(self.vocab)), dtype=float)

    def fit_transform(self, textos: Sequence[Any]) -> np.ndarray:
        return self.fit(textos).transform(textos)

    def estado(self) -> Dict[str, Any]:
        return {"vocab": self.vocab, "idf_": self.idf_.tolist()}

    def __repr__(self):
        return f"<VectorizadorTFIDF vocab={len(self.vocab)}>"


def _texto_evento(ev: Dict) -> str:
    """Texto representativo de un evento (nombre + lugar + organizador + fuente)."""
    return " ".join(_limpiar(ev.get(c)) for c in CAMPOS_TEXTO)


def vectorizar_textos(lista_de_textos: Sequence[Any]) -> np.ndarray:
    """Entrena (una sola vez, guardando el modelo) y devuelve la matriz TF-IDF."""
    vectorizador = VectorizadorTFIDF()
    if not lista_de_textos:
        _guardar_modelo(TFIDF_PATH, vectorizador.estado())
        return vectorizador.transform([])
    matriz = vectorizador.fit_transform(lista_de_textos)
    _guardar_modelo(TFIDF_PATH, vectorizador.estado())
    return matriz


# ---------------------------------------------------------------------------
# 2) Deduplicación semántica
# ---------------------------------------------------------------------------
def deduplicar_semanticamente(eventos_nuevos: List[Dict],
                              eventos_existentes: List[Dict],
                              umbral: float = UMBRAL_SIMILITUD) -> List[Dict]:
    """Devuelve solo los eventos realmente nuevos.

    Compara únicamente los NOMBRES de los eventos (no el lugar/organizador,
    que dispara falsos positivos en eventos distintos del mismo recinto).
    Si la similitud máxima supera `umbral` Y las fechas coinciden (o +/-1
    día), es duplicado y se descarta. Si las fechas no coinciden, se
    conserva (evita borrar series semanales como "Elements Cave").
    """
    if not eventos_nuevos or not eventos_existentes:
        return list(eventos_nuevos)
    nombres_n = [_limpiar(e.get("nombre", "")) for e in eventos_nuevos]
    nombres_e = [_limpiar(e.get("nombre", "")) for e in eventos_existentes]
    try:
        vec = VectorizadorTFIDF()
        X = vec.fit_transform(nombres_e + nombres_n)
        n_e = len(eventos_existentes)
        Xe = X[:n_e]
        Xn = X[n_e:]
        sim = Xn @ Xe.T  # matrices L2 → producto escalar = coseno
    except Exception:
        return list(eventos_nuevos)
    nuevos_filtrados = []
    for i, ev in enumerate(eventos_nuevos):
        if sim.shape[1] == 0:
            nuevos_filtrados.append(ev)
            continue
        max_sim = float(np.max(sim[i]))
        if max_sim < umbral:
            nuevos_filtrados.append(ev)
            continue
        j = int(np.argmax(sim[i]))
        if _mismas_fechas(ev.get("fecha"), eventos_existentes[j].get("fecha")):
            continue  # duplicado → descartar
        nuevos_filtrados.append(ev)
    return nuevos_filtrados


# ---------------------------------------------------------------------------
# Clasificador softmax (regresión logística multiclase) + guardado persistente
# ---------------------------------------------------------------------------
class ClasificadorSoftmax:
    """Regresión logística multiclase (softmax) con descenso de gradiente."""

    def __init__(self, lr: float = 0.3, epocas: int = 400, reg: float = 0.01):
        self.lr = lr
        self.epocas = epocas
        self.reg = reg
        self.clases: List[str] = []
        self.W: Optional[np.ndarray] = None
        self.b: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray, y: Sequence[str]) -> "ClasificadorSoftmax":
        clases = list(dict.fromkeys(str(c) for c in y))
        if len(clases) < 2 or X.shape[1] == 0 or len(y) < 2:
            self.clases = clases
            return self
        self.clases = clases
        mapa = {c: i for i, c in enumerate(clases)}
        Y = np.zeros((X.shape[0], len(clases)))
        for i, c in enumerate(y):
            Y[i, mapa[c]] = 1.0
        n = X.shape[0]
        d, k = X.shape[1], len(clases)
        W = np.zeros((d, k))
        b = np.zeros(k)
        for _ in range(self.epocas):
            logits = X @ W + b
            exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
            p = exp / np.sum(exp, axis=1, keepdims=True)
            grad_W = (X.T @ (p - Y)) / n + self.reg * W
            grad_b = np.mean(p - Y, axis=0)
            W -= self.lr * grad_W
            b -= self.lr * grad_b
        self.W = W
        self.b = b
        return self

    def predict_proba(self, X: np.ndarray) -> Tuple[List[str], np.ndarray]:
        if not self.clases or self.W is None:
            return self.clases, np.zeros((X.shape[0], 0), dtype=float)
        logits = X @ self.W + self.b
        exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
        prob = exp / np.sum(exp, axis=1, keepdims=True)
        return self.clases, prob

    def estado(self) -> Dict[str, Any]:
        return {
            "clases": self.clases,
            "W": self.W.tolist() if self.W is not None else None,
            "b": self.b.tolist() if self.b is not None else None,
        }

    @classmethod
    def desde_estado(cls, estado: Dict[str, Any]) -> "ClasificadorSoftmax":
        obj = cls()
        obj.clases = list(estado.get("clases", []))
        obj.W = np.array(estado["W"]) if estado.get("W") else None
        obj.b = np.array(estado["b"]) if estado.get("b") else None
        return obj

    def __repr__(self):
        return f"<ClasificadorSoftmax clases={len(self.clases)}>"


# ---------------------------------------------------------------------------
# Control de modelos (pickle, tolerante a fallos)
# ---------------------------------------------------------------------------
def _guardar_modelo(ruta: Path, datos: Any) -> None:
    try:
        tmp = str(ruta) + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(datos, f)
        os.replace(tmp, str(ruta))
    except (IOError, OSError, pickle.PickleError, Exception):
        pass


def _cargar_modelo(ruta: Path, por_defecto: Any = None) -> Any:
    if not ruta.exists():
        return por_defecto
    try:
        with open(ruta, "rb") as f:
            return pickle.load(f)
    except Exception:
        return por_defecto


def _contador_ejecuciones() -> int:
    if CONTADOR_PATH.exists():
        try:
            return int(CONTADOR_PATH.read_text(encoding="utf-8").strip() or "0")
        except (ValueError, IOError):
            return 0
    return 0


def _incrementar_contador() -> int:
    n = _contador_ejecuciones() + 1
    try:
        CONTADOR_PATH.write_text(str(n), encoding="utf-8")
    except IOError:
        pass
    return n


# ---------------------------------------------------------------------------
# 3) Subgénero
# ---------------------------------------------------------------------------
def _entrenar_subgenero(eventos: List[Dict]) -> Optional[ClasificadorSoftmax]:
    etiquetados = [ev for ev in eventos
                   if _limpiar(ev.get("subgenero", "")) not in SUBGENEROS_NO_PSY]
    if len(etiquetados) < 8:
        return None
    textos = [_texto_evento(e) for e in etiquetados]
    try:
        vec = VectorizadorTFIDF().fit(textos)
        X = vec.transform(textos)
        if X.shape[1] == 0 or X.shape[0] < 2:
            return None
        clf = ClasificadorSoftmax().fit(X, [ev["subgenero"] for ev in etiquetados])
        if len(clf.clases) < 2:
            return None
        _guardar_modelo(SUBGENERO_PATH, {"vectorizador": vec.estado(),
                                         "clasificador": clf.estado()})
        return clf
    except Exception:
        return None


def predecir_subgenero(texto_evento: str) -> Tuple[str, float]:
    modelo = _cargar_modelo(SUBGENERO_PATH)
    if not modelo or not modelo.get("vectorizador"):
        return "N/A", 0.0
    return _predecir(texto_evento, modelo)


def _cargar_vectorizador(estado: Dict) -> VectorizadorTFIDF:
    vec = VectorizadorTFIDF()
    vec.vocab = list(estado.get("vocab", []))
    vec.idf_ = np.array(estado.get("idf_", []), dtype=float)
    vec._indice = {w: i for i, w in enumerate(vec.vocab)}
    return vec


def _predecir(texto_evento: str, modelo: Dict) -> Tuple[str, float]:
    try:
        vec = _cargar_vectorizador(modelo["vectorizador"])
        clf = ClasificadorSoftmax.desde_estado(modelo["clasificador"])
        X = vec.transform([texto_evento])
        clases, prob = clf.predict_proba(X)
        if not clases or prob.shape[1] == 0:
            return "N/A", 0.0
        i = int(np.argmax(prob[0]))
        return clases[i], float(prob[0, i])
    except Exception:
        return "N/A", 0.0


# ---------------------------------------------------------------------------
# 4) Tipo de lugar (indoor/outdoor)
# ---------------------------------------------------------------------------
def _regla_tipo_lugar(texto: str) -> Optional[str]:
    """Reglas heurísticas claras. Devuelve 'indoor'/'outdoor' o None."""
    t = _limpiar(texto)
    indoor = ("club", " bar ", "lounge", "gym", "venue", "hall", "theatre",
              "theater", "arena", "stadium", "pub", "restaurant", "basement",
              "warehouse party", "nightclub", "discoteca", "indoor")
    outdoor = ("festival", "beach", "forest", "open air", "open-air",
               "outdoor", "park", "garden", "camping", "campsite", "valley",
               "mountain", "riverbank", "field", "meadow", "lake", "island",
               "desert", "campsite")
    if any(w in t for w in indoor):
        return "indoor"
    if any(w in t for w in outdoor):
        return "outdoor"
    return None


def _entrenar_tipo_lugar(eventos: List[Dict]) -> Optional[ClasificadorSoftmax]:
    pares = []
    for ev in eventos:
        texto = _texto_evento(ev) + " " + _limpiar(ev.get("lugar", ""))
        t = _regla_tipo_lugar(texto)
        if t:
            pares.append((texto, t))
    if len(pares) < 8:
        return None
    textos, etiquetas = zip(*pares)
    try:
        vec = VectorizadorTFIDF().fit(textos)
        X = vec.transform(textos)
        if X.shape[1] == 0 or len(set(etiquetas)) < 2:
            return None
        clf = ClasificadorSoftmax().fit(X, etiquetas)
        if len(clf.clases) < 2:
            return None
        _guardar_modelo(TIPO_LUGAR_PATH, {"vectorizador": vec.estado(),
                                          "clasificador": clf.estado()})
        return clf
    except Exception:
        return None


def predecir_tipo_lugar(texto_evento: str) -> Tuple[str, float]:
    t = _regla_tipo_lugar(texto_evento)
    if t:
        return t, 1.0
    modelo = _cargar_modelo(TIPO_LUGAR_PATH)
    if not modelo or not modelo.get("vectorizador"):
        return "N/A", 0.0
    return _predecir(texto_evento, modelo)


# ---------------------------------------------------------------------------
# 5) Supervisión global + entrenamiento automático
# ---------------------------------------------------------------------------
def _necesita_reentrenar() -> bool:
    sin_modelos = not (SUBGENERO_PATH.exists() and TIPO_LUGAR_PATH.exists())
    return sin_modelos or (_contador_ejecuciones() % RETRAIN_CADA == 0)


def supervisar_y_mejorar(csv_path: str = "eventos_encontrados.csv",
                         aplicar_cambios: bool = True) -> Dict[str, Any]:
    resultado = {
        "timestamp": datetime.now().isoformat(),
        "csv": csv_path,
        "aplicar_cambios": aplicar_cambios,
        "duplicados_semanticos": [],
        "subgeneros_rellenados": [],
        "tipos_lugar_rellenados": [],
    }

    filename = Path(csv_path) if csv_path else (PROJECT_ROOT / "eventos_encontrados.csv")
    if not filename.exists():
        resultado["error"] = f"CSV no existe: {filename}"
        return resultado

    import csv as _csv
    try:
        with open(filename, "r", encoding="utf-8") as f:
            eventos = [dict(r) for r in _csv.DictReader(f)]
    except Exception as e:
        resultado["error"] = f"No se puede leer el CSV: {e}"
        return resultado
    if not eventos:
        resultado["error"] = "CSV vacío."
        return resultado

    if _necesita_reentrenar():
        try:
            _entrenar_subgenero(eventos)
            _entrenar_tipo_lugar(eventos)
        except Exception:
            pass

    # --- Deduplicación semántica interna ---
    try:
        _dedup_semantico_interno(eventos, resultado, umbral=UMBRAL_SIMILITUD)
    except Exception:
        pass

    # --- Rellenar subgénero N/A y tipo_lugar ---
    tiene_tipo = "tipo_lugar" in (eventos[0].keys())
    if not tiene_tipo:
        for ev in eventos:
            ev["tipo_lugar"] = "N/A"
    for ev in eventos:
        texto = _texto_evento(ev)
        # subgénero
        cur = _limpiar(ev.get("subgenero", ""))
        if cur in ("n/a", "na", ""):
            pred, conf = predecir_subgenero(texto)
            if pred != "N/A" and conf >= UMBRAL_CONFIANZA:
                ev["subgenero"] = pred
                resultado["subgeneros_rellenados"].append({
                    "nombre": ev.get("nombre"), "confianza": round(conf, 3),
                })
            elif cur in ("na", ""):
                ev["subgenero"] = "N/A"
        # tipo de lugar
        curt = _limpiar(ev.get("tipo_lugar", ""))
        if curt not in ("indoor", "outdoor"):
            pred_t, conf_t = predecir_tipo_lugar(texto)
            if pred_t != "N/A" and conf_t >= UMBRAL_CONFIANZA:
                ev["tipo_lugar"] = pred_t
                resultado["tipos_lugar_rellenados"].append({
                    "nombre": ev.get("nombre"), "tipo": pred_t,
                    "confianza": round(conf_t, 3),
                })

    if aplicar_cambios:
        claves = list(eventos[0].keys())
        if "tipo_lugar" not in claves:
            claves.append("tipo_lugar")
        try:
            tmp = str(filename) + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                writer = _csv.DictWriter(f, fieldnames=claves, extrasaction="ignore")
                writer.writeheader()
                for ev in eventos:
                    writer.writerow(ev)
            os.replace(tmp, str(filename))
        except Exception as e:
            resultado["error"] = f"No se puede escribir el CSV: {e}"
        try:
            tmp = str(REPORTE_PATH) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(resultado, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(REPORTE_PATH))
        except Exception:
            pass

    _incrementar_contador()
    resultado["resumen"] = {
        "filas_finales": len(eventos),
        "duplicados_eliminados": len(resultado["duplicados_semanticos"]),
        "subgeneros_rellenados": len(resultado["subgeneros_rellenados"]),
        "tipos_lugar_rellenados": len(resultado["tipos_lugar_rellenados"]),
    }
    return resultado


def _dedup_semantico_interno(eventos: List[Dict], resultado: Dict[str, Any],
                             umbral: float = UMBRAL_NOMBRE) -> None:
    """Elimina duplicados internos (nombre casi idéntico + fechas iguales/±1).

    Usa SOLO el nombre del evento y un umbral alto (0.92) para no fusionar
    eventos distintos del mismo recinto (p. ej. "Housy at Noxe" vs "HOPE at
    Noxe"). Nunca toca eventos con fecha no definida.
    """
    marcar = set()
    for i in range(len(eventos)):
        if i in marcar:
            continue
        for j in range(i + 1, len(eventos)):
            if j in marcar:
                continue
            if not _mismas_fechas(eventos[i].get("fecha"), eventos[j].get("fecha")):
                continue
            sim = _similitud_dos(_limpiar(eventos[i].get("nombre", "")),
                                 _limpiar(eventos[j].get("nombre", "")))
            if sim >= umbral:
                marcar.add(j)
                resultado["duplicados_semanticos"].append({
                    "conservado": eventos[i].get("nombre"),
                    "eliminado": eventos[j].get("nombre"),
                    "similitud": round(sim, 3),
                    "fecha": eventos[j].get("fecha"),
                })
    # NOTA: no se eliminan filas del CSV. Regla innegociable del proyecto:
    # "sumar nunca restar" — la supervisión solo RELLENA campos N/A, no borra
    # eventos (ni siquiera duplicados semánticos, para no perder cobertura de
    # fuentes). Se registran en el reporte pero NO se quitan del listado.
    # if marcar:
    #     eventos[:] = [ev for idx, ev in enumerate(eventos) if idx not in marcar]


def _similitud_dos(a: str, b: str) -> float:
    try:
        vec = VectorizadorTFIDF()
        X = vec.fit_transform([a, b])
        if X.shape[1] == 0:
            return 0.0
        return float(X[0] @ X[1])
    except Exception:
        return 0.0


if __name__ == "__main__":
    res = supervisar_y_mejorar(aplicar_cambios=True)
    print(json.dumps(res.get("resumen", res), ensure_ascii=False, indent=2))