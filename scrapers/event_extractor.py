#!/usr/bin/env python3
"""
Extractor avanzado de eventos desde texto plano.
Sin NLP, 100% basado en patrones. Soporta fechas en múltiples formatos,
extracción de ubicaciones, nombres de eventos, organizadores, enlaces,
clasificación de tipo de venue (indoor/outdoor/club/etc.) y países.
"""

import re
import json
from datetime import datetime, timedelta

MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

MONTHS_RU = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}

MONTHS_DE = {
    "jan": 1, "feb": 2, "mär": 3, "apr": 4, "mai": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12,
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}

ALL_MONTHS = {**MONTHS_ES, **MONTHS_EN, **MONTHS_RU, **MONTHS_DE}
MONTH_PATTERN = "|".join(sorted(ALL_MONTHS.keys(), key=len, reverse=True))

EVENT_TRIGGERS = [
    "party", "festival", "event", "gathering", "ceremony",
    "full moon", "fest", "edition", "takeover", "open air",
    "solstice", "equinox", "trance mission", "bush doof",
    "outdoor", "rave", "doof", "ritual", "celebration",
    "showcase", "night", "experience", "journey", "voyage",
    "conference", "retreat", "workshop",
]

VENUE_TYPE_KEYWORDS = {
    "indoor": ["indoor", "interior", "inside", "sala", "hall", "room", "local"],
    "outdoor": ["outdoor", "exterior", "outside", "aire libre", "open air",
                "al aire libre"],
    "club": ["club", "discoteca", "disco", "nightclub", "sala de fiestas",
             "dancing"],
    "forest": ["forest", "bosque", "selva", "jungle", "woodland", "trees"],
    "beach": ["beach", "playa", "costa", "shore", "coast", "seaside"],
    "warehouse": ["warehouse", "nave", "fabbrica", "industrial", "polígono"],
    "open air": ["open air", "open-air", "al aire libre", "outdoor venue"],
    "festival": ["festival", "fest", "fiesta", "macrofiesta"],
    "mountain": ["mountain", "montaña", "sierra", "peak", "cima", "collado"],
    "river": ["river", "río", "ribeira", "banks", "orilla"],
    "urban": ["urban", "urbano", "city", "ciudad", "centro", "downtown"],
    "camping": ["camping", "camp", "acampada", "tienda", "carpa", "tent"],
    "temple": ["temple", "templo", "shrine", "santuario"],
    "garden": ["garden", "jardín", "parque", "park", "botanical"],
    "rooftop": ["rooftop", "terrazza", "azotea", "roof", "piso superior"],
    "vineyard": ["vineyard", "viñedo", "bodega", "winery"],
    "lake": ["lake", "lago", "embalse", "reservoir"],
}

# Ciudades europeas principales para detección (expandida)
KNOWN_CITIES = {
    "berlin": ("Berlín", "Alemania"), "berlín": ("Berlín", "Alemania"),
    "hamburg": ("Hamburgo", "Alemania"), "hamburgo": ("Hamburgo", "Alemania"),
    "munich": ("Múnich", "Alemania"), "múnich": ("Múnich", "Alemania"),
    "colonia": ("Colonia", "Alemania"), "frankfurt": ("Frankfurt", "Alemania"),
    "stuttgart": ("Stuttgart", "Alemania"), "düsseldorf": ("Düsseldorf", "Alemania"),
    "leipzig": ("Leipzig", "Alemania"), "dortmund": ("Dortmund", "Alemania"),
    "barcelona": ("Barcelona", "España"), "madrid": ("Madrid", "España"),
    "valencia": ("Valencia", "España"), "sevilla": ("Sevilla", "España"),
    "málaga": ("Málaga", "España"), "bilbao": ("Bilbao", "España"),
    "lisboa": ("Lisboa", "Portugal"), "porto": ("Oporto", "Portugal"),
    "paris": ("París", "Francia"), "parís": ("París", "Francia"),
    "lyon": ("Lyon", "Francia"), "toulouse": ("Toulouse", "Francia"),
    "marsella": ("Marsella", "Francia"), "bordeaux": ("Burdeos", "Francia"),
    "lille": ("Lille", "Francia"), "nantes": ("Nantes", "Francia"),
    "roma": ("Roma", "Italia"), "milan": ("Milán", "Italia"),
    "milán": ("Milán", "Italia"), "napoli": ("Nápoles", "Italia"),
    "torino": ("Turín", "Italia"), "bologna": ("Boloña", "Italia"),
    "firenze": ("Florencia", "Italia"), "venezia": ("Venecia", "Italia"),
    "amsterdam": ("Ámsterdam", "Países Bajos"), "rotterdam": ("Róterdam", "Países Bajos"),
    "utrecht": ("Utrecht", "Países Bajos"), "eindhoven": ("Eindhoven", "Países Bajos"),
    "london": ("Londres", "Reino Unido"), "londres": ("Londres", "Reino Unido"),
    "manchester": ("Mánchester", "Reino Unido"), "bristol": ("Bristol", "Reino Unido"),
    "edinburgh": ("Edimburgo", "Reino Unido"), "glasgow": ("Glasgow", "Reino Unido"),
    "vienna": ("Viena", "Austria"), "viena": ("Viena", "Austria"),
    "zurich": ("Zúrich", "Suiza"), "zúrich": ("Zúrich", "Suiza"),
    "geneva": ("Ginebra", "Suiza"), "ginebra": ("Ginebra", "Suiza"),
    "stockholm": ("Estocolmo", "Suecia"), "oslo": ("Oslo", "Noruega"),
    "copenhagen": ("Copenhague", "Dinamarca"), "copenhague": ("Copenhague", "Dinamarca"),
    "warsaw": ("Varsovia", "Polonia"), "varsovia": ("Varsovia", "Polonia"),
    "krakow": ("Cracovia", "Polonia"), "cracovia": ("Cracovia", "Polonia"),
    "prague": ("Praga", "República Checa"), "praga": ("Praga", "República Checa"),
    "budapest": ("Budapest", "Hungría"), "athens": ("Atenas", "Grecia"),
    "atenas": ("Atenas", "Grecia"), "thessaloniki": ("Salónica", "Grecia"),
    "helsinki": ("Helsinki", "Finlandia"), "dublin": ("Dublín", "Irlanda"),
    "bucarest": ("Bucarest", "Rumanía"), "zagreb": ("Zagreb", "Croacia"),
    "sofia": ("Sofía", "Bulgaria"), "belgrade": ("Belgrado", "Serbia"),
    "belgrado": ("Belgrado", "Serbia"), "kyiv": ("Kiev", "Ucrania"),
    "kiev": ("Kiev", "Ucrania"), "tbilisi": ("Tiflis", "Georgia"),
    "istanbul": ("Estambul", "Turquía"), "estambul": ("Estambul", "Turquía"),
    "tel aviv": ("Tel Aviv", "Israel"), "tokyo": ("Tokio", "Japón"),
    "sao paulo": ("São Paulo", "Brasil"), "buenos aires": ("Buenos Aires", "Argentina"),
    "mexico city": ("Ciudad de México", "México"), "ciudad de méxico": ("Ciudad de México", "México"),
    "bogota": ("Bogotá", "Colombia"), "bogotá": ("Bogotá", "Colombia"),
    "santiago": ("Santiago", "Chile"), "lima": ("Lima", "Perú"),
    "sydney": ("Sídney", "Australia"), "melbourne": ("Melbourne", "Australia"),
}

COUNTRIES_ES = [
    "españa", "alemania", "francia", "italia", "portugal", "bélgica",
    "países bajos", "holanda", "reino unido", "inglaterra", "suiza",
    "austria", "suecia", "noruega", "dinamarca", "polonia", "grecia",
    "turquía", "hungría", "croacia", "bulgaria", "rumanía", "ucrania",
    "república checa", "méxico", "brasil", "argentina",
    "chile", "colombia", "perú", "uruguay", "costa rica", "india",
    "tailandia", "japón", "australia", "nueva zelanda", "canadá",
    "estados unidos", "marruecos", "egipto", "israel",
    "eslovaquia", "eslovenia", "estonia", "letonia", "lituania",
    "finlandia", "irlanda", "islandia", "serbia", "bosnia",
    "montenegro", "kosovo", "macedonia", "albania", "moldavia",
    "bielorrusia", "chipre", "malta", "luxemburgo",
]

# Sinónimos de subgéneros para ampliar la clasificación por texto.
# Las claves son términos que aparecen en títulos/descripciones; los valores
# son el subgénero canónico al que mapean. Orden = prioridad de coincidencia.
SINONIMOS = {
    "dark": "darkpsy",
    "darkpsy": "darkpsy",
    "dark psy": "darkpsy",
    "dark-psy": "darkpsy",
    "dark-psytrance": "darkpsy",
    "forest": "forest",
    "forest psy": "forest",
    "forest-psy": "forest",
    "forestpsy": "forest",
    "goa": "goa",
    "goa trance": "goa",
    "goatrance": "goa",
    "fullon": "fullon",
    "full-on": "fullon",
    "full on": "fullon",
    "progressive": "progressive",
    "prog": "progressive",
    "prog psy": "progressive",
    "progressive trance": "progressive",
    "hitech": "hitech",
    "hi-tech": "hitech",
    "hi tech": "hitech",
    "psychill": "psychill",
    "psy-chill": "psychill",
    "psy chill": "psychill",
    "psybient": "psybient",
    "psytrance": "psytrance",
    "psy trance": "psytrance",
    "psychedelic": "psychedelic",
    "psychedelic trance": "psychedelic",
    "twilight": "twilight",
    "twilight psy": "twilight",
    "psycore": "psycore",
    "psy core": "psycore",
    "suomisaundi": "suomisaundi",
    "suomi": "suomisaundi",
    "suomi psy": "suomisaundi",
    "zenon": "zenon",
    "zenon psy": "zenon",
}

# Subgéneros base con coincidencia por substring (comportamiento original).
SUBGENEROS_BASE = list(dict.fromkeys(SINONIMOS.values()))

# Mapeo de fragmentos conocidos de festivales/collectivos a subgénero.
# Se usa como señal de refuerzo cuando el nombre del organizador o del evento
# incluye estos términos. Prioriza coincidencias exactas de nombre.
FESTIVAL_SUGIERE = {
    "ozora": "psytrance",
    "boom festival": "psytrance",
    "boom": "psytrance",
    "saga": "progressive",
    "the Meadow": "progressive",
    "Vuu": "forest",
    "Anthema": "progressive",
    "Antheia": "forest",
    "Hydra": "forest",
    "Tsunami": "darkpsy",
    "Carp": "darkpsy",
    "Kosa": "darkpsy",
    "Psyland": "forest",
    "Shivanandi": "psytrance",
    "Moksha": "psytrance",
    "Whitenoise": "darkpsy",
}


# Mapeo de frases/alias de ALTA precisión a subgénero. Se usa en la estrategia
# ponderada (clasificación por campos auxiliares) evitando falsos positivos:
# términos cortos como "goa" o "forest" aparecen como subcadena en nombres que
# no son psyclub. Se exige coincidencia por límites de palabra.
PALABRAS_CLAVE_PRECISAS = {
    "darkpsy": "darkpsy",
    "dark psy": "darkpsy",
    "forest psytrance": "forest",
    "forest psy": "forest",
    "psychill": "psychill",
    "psybient": "psybient",
    "fullon": "fullon",
    "full on": "fullon",
    "progressive psytrance": "progressive",
    "progressive psy": "progressive",
    "goa trance": "goa",
    "goatrance": "goa",
    "hitech psytrance": "hitech",
    "twilight": "twilight",
    "psycore": "psycore",
    "suomisaundi": "suomisaundi",
    "zenon psytrance": "zenon",
    "psychedelic": "psychedelic",
    "psytrance": "psytrance",
    "psy trance": "psytrance",
}


def _buscar_subgenero_en_texto(texto, usar_sinonimos):
    """Devuelve el primer subgénero hallado con alta precisión (word-boundary).

    Prioriza frases de alta precisión (PALABRAS_CLAVE_PRECISAS) y evita
    subcadenas ambigüas. Se usa en la estrategia ponderada de
    `clasificar_subgenero`.
    """
    if not texto:
        return None
    texto_lower = texto.lower()
    for frase, subgenero in PALABRAS_CLAVE_PRECISAS.items():
        if re.search(r"\b" + re.escape(frase) + r"\b", texto_lower):
            return subgenero
    if usar_sinonimos:
        for sinonimo, subgenero in SINONIMOS.items():
            if re.search(r"\b" + re.escape(sinonimo) + r"\b", texto_lower):
                return subgenero
    return None


class EventExtractor:
    def extract_all(self, text, source_name="", source_url=""):
        if not text or len(text.strip()) < 30:
            return []

        results = []
        paragraphs = re.split(r"\n\s*\n", text)
        for para in paragraphs:
            ev = self._extract_single(para, source_name, source_url)
            if ev:
                results.append(ev)
        return results

    def clasificar_subgenero(self, texto: str, organizador: str = "",
                             lugar: str = "", descripcion: str = "",
                             email: str = "", link: str = "",
                             usar_sinonimos: bool = True,
                             peso_titulo: int = 3,
                             peso_org: int = 2,
                             peso_lugar: int = 1,
                             peso_desc: int = 1,
                             peso_email: int = 1,
                             peso_link: int = 1,
                             umbral: int = 2) -> str:
        """Clasifica un evento en un subgénero específico.

        Estrategias en orden de prioridad:
        1. Coincidencia por substring con los subgéneros base (comportamiento
           original, no se altera).
        2. Coincidencia con límites de palabra sobre SINONIMOS, que amplía
           con términos como "dark", "prog", "full-on" o "hi-tech".
        3. (Ampliación) Búsqueda por pesos sobre campos auxiliares. Si el evento
           ya no fue clasificado por las 2 primeras estrategias, se exploran los
           campos organizador, lugar, descripcion, email y link. Cada coincidencia
           suma un peso al subgénero candidato; gana el de mayor puntuación
           (siempre que alcance el `umbral`).

        Los parámetros de peso/umbral permiten que el loop de mejora pruebe
        combinaciones. El valor por defecto conserva el comportamiento anterior
        (titulo pesa 3, sinónimos activados), por lo que no hay regresión.
        """
        if not texto and not organizador and not lugar and not descripcion:
            return "general"

        texto_lower = (texto or "").lower()

        # Estrategia 1: substring (comportamiento original)
        for subgenero in SUBGENEROS_BASE:
            if subgenero in texto_lower:
                return subgenero

        # Estrategia 2: sinónimos con límites de palabra (ampliación)
        if usar_sinonimos:
            for sinonimo, subgenero in SINONIMOS.items():
                if re.search(r"\b" + re.escape(sinonimo) + r"\b", texto_lower):
                    return subgenero

        # Estrategia 3: puntuación ponderada sobre campos auxiliares.
        # Sólo se usa si la estrategia 1/2 no clasificó (texto_lower no tuvo match).
        campos = (
            (texto_lower, peso_titulo),
            ((organizador or "").lower(), peso_org),
            ((lugar or "").lower(), peso_lugar),
            ((descripcion or "").lower(), peso_desc),
            ((email or "").lower(), peso_email),
            ((link or "").lower(), peso_link),
        )
        puntuaciones: dict = {}
        for contenido, peso in campos:
            if not contenido:
                continue
            sub = _buscar_subgenero_en_texto(contenido, usar_sinonimos)
            if sub:
                puntuaciones[sub] = puntuaciones.get(sub, 0) + peso
        if puntuaciones:
            mejor = max(puntuaciones, key=puntuaciones.get)
            if puntuaciones[mejor] >= umbral:
                return mejor
        return "general"

    def _extract_single(self, text, source_name, source_url):
        text = text.strip()
        text_lower = text.lower()
        if len(text) < 30:
            return None
        if not any(t in text_lower for t in EVENT_TRIGGERS):
            return None

        fecha = self._extract_date(text)
        lugar = self._extract_location(text)
        ciudad, pais = self._extract_city_country(text, lugar)
        tipo_lugar = self._extract_venue_type(text)
        nombre = self._extract_name(text)
        organizador = self._extract_organizer(text, source_name)
        email = self._extract_email(text)
        precio = self._extract_price(text)
        enlaces = self._extract_links(text)
        descripcion = text[:150].strip()

        ev = {
            "nombre": nombre or text[:80].strip(),
            "fecha": fecha or "N/A",
            "lugar": lugar or "N/A",
            "ciudad": ciudad or "N/A",
            "pais": pais or "N/A",
            "tipo_lugar": tipo_lugar or "N/A",
            "fuente": f"Facebook ({source_name})" if source_name else "Facebook",
            "organizador": organizador or source_name or "N/A",
            "email": email or "N/A",
            "url": source_url or "N/A",
            "descripcion": descripcion,
            "precio": precio,
            "enlaces": enlaces,
        }
        return ev

    # ------------------------------------------------------------------ #
    # FECHA
    # ------------------------------------------------------------------ #
    SP = r"[ ]"

    def _extract_date(self, text):
        flat = re.sub(r"\s+", " ", text)

        patterns = [
            (r"(\d{4}-\d{2}-\d{2})", 1),
            (r"(\d{4})\.(\d{1,2})\.(\d{1,2})", 10),
            (r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", 2),
            (r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})", 3),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}})-(\d{{1,2}}),{self.SP}*(\d{{4}})", 9),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}})(?:st|nd|rd|th),{self.SP}*(\d{{4}})", 5),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}}),{self.SP}*(\d{{4}})", 5),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}}){self.SP}+(\d{{4}})", 5),
            (rf"(\d{{1,2}}){self.SP}*de{self.SP}+({MONTH_PATTERN}){self.SP}*de{self.SP}*(\d{{4}})", 4),
            (rf"(\d{{1,2}})(?:st|nd|rd|th)?{self.SP}+({MONTH_PATTERN}){self.SP}+(\d{{4}})", 4),
            (rf"(\d{{1,2}})[./-]({MONTH_PATTERN})[./-](\d{{4}})", 6),
            (rf"(\d{{1,2}})[-](\d{{1,2}}){self.SP}+({MONTH_PATTERN}){self.SP}+(\d{{4}})", 8),
            (rf"(\d{{4}})[-/]({MONTH_PATTERN})[/-](\d{{1,2}})", 11),
            # Formatos con "of" y ordinales sin año (usar año actual)
            (rf"(\d{{1,2}})(?:st|nd|rd|th)?{self.SP}+of{self.SP}+({MONTH_PATTERN})", 12),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}})(?:st|nd|rd|th)", 13),
            # Formatos con ordinales sin "of" ni año (usar año actual)
            (rf"(\d{{1,2}})(?:st|nd|rd|th){self.SP}+({MONTH_PATTERN})", 14),
            (rf"({MONTH_PATTERN}){self.SP}+(\d{{1,2}})(?![,\d])", 15),
            (rf"(?<!\d)(\d{{1,2}}){self.SP}+({MONTH_PATTERN})", 7),
            # Formatos alemanes: "6. August 2026", "vom 6. bis 9. August 2026"
            (rf"(\d{{1,2}})\.\s*bis\s+(\d{{1,2}})\.\s+({MONTH_PATTERN})\s+(\d{{4}})", 16),
            (rf"(?:vom\s+)?(\d{{1,2}})\.\s+({MONTH_PATTERN})\s+(\d{{4}})", 17),
            (rf"(\d{{1,2}})\.\s+({MONTH_PATTERN})", 18),
        ]

        for pat, fmt in patterns:
            m = re.search(pat, flat, re.IGNORECASE)
            if m:
                try:
                    return self._format_date(m, fmt)
                except (ValueError, IndexError):
                    continue
        return None

    def _format_date(self, m, fmt):
        now = datetime.now()
        if fmt == 1:
            return m.group(1)
        elif fmt == 2:
            d, mo, y = m.group(1), m.group(2), m.group(3)
            if not (1 <= int(mo) <= 12):
                raise ValueError("mes fuera de rango")
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        elif fmt == 3:
            d, mo, y = m.group(1), m.group(2), m.group(3)
            if not (1 <= int(mo) <= 12):
                raise ValueError("mes fuera de rango")
            y = "20" + y if int(y) < 100 else y
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        elif fmt == 4:
            d, mo_text, y = m.group(1), m.group(2).lower()[:3], m.group(3)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 5:
            mo_text, d, y = m.group(1).lower()[:3], m.group(2), m.group(3)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 6:
            d, mo_text, y = m.group(1), m.group(2).lower()[:3], m.group(3)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 7:
            d, mo_text = m.group(1), m.group(2).lower()[:3]
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 8:
            d1, d2, mo_text, y = m.group(1), m.group(2), m.group(3).lower()[:3], m.group(4)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d1):02d}"
        elif fmt == 9:
            mo_text, d1, d2, y = m.group(1).lower()[:3], m.group(2), m.group(3), m.group(4)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d1):02d}"
        elif fmt == 10:
            y, mo, d = m.group(1), m.group(2), m.group(3)
            if not (1 <= int(mo) <= 12):
                raise ValueError("mes fuera de rango")
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        elif fmt == 11:
            y, mo_text, d = m.group(1), m.group(2).lower()[:3], m.group(3)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 12:
            # "9th of May" -> d, mo_text (no year, use current)
            d, mo_text = m.group(1), m.group(2).lower()[:3]
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 13:
            # "May 9th" -> mo_text, d (no year, use current)
            mo_text, d = m.group(1).lower()[:3], m.group(2)
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 14:
            # "9th May" -> d, mo_text (no year, use current)
            d, mo_text = m.group(1), m.group(2).lower()[:3]
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 15:
            # "May 9" -> mo_text, d (no year, use current)
            mo_text, d = m.group(1).lower()[:3], m.group(2)
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 16:
            # "6. bis 9. August 2026" -> d1, d2, mo_text, y
            d1, d2, mo_text, y = m.group(1), m.group(2), m.group(3).lower()[:3], m.group(4)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d1):02d}"
        elif fmt == 17:
            # "vom 6. August 2026" or "6. August 2026" -> d, mo_text, y
            d, mo_text, y = m.group(1), m.group(2).lower()[:3], m.group(3)
            mo = ALL_MONTHS.get(mo_text, 1)
            return f"{y}-{mo:02d}-{int(d):02d}"
        elif fmt == 18:
            # "6. August" -> d, mo_text (no year, use current)
            d, mo_text = m.group(1), m.group(2).lower()[:3]
            mo = ALL_MONTHS.get(mo_text, 1)
            y = now.year
            return f"{y}-{mo:02d}-{int(d):02d}"
        return None

    # ------------------------------------------------------------------ #
    # UBICACIÓN
    # ------------------------------------------------------------------ #
    FALSE_POSITIVES = {
        "organized", "organised", "organizador", "organizado", "more",
        "location", "contact", "date", "festival", "event", "party",
        "january", "february", "march", "april", "june", "july", "august",
        "september", "october", "november", "december", "jan", "feb",
        "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
        "enero", "febrero", "marzo", "abril", "junio", "julio", "agosto",
        "info", "entry", "price", "donation", "ticket", "time", "next",
    }

    def _extract_location(self, text):
        city_word = r"[A-Z][a-zA-ZáéíóúñüÁÉÍÓÚÑÜ']+"
        patterns = [
            rf"@\s*({city_word}(?:\s{city_word})*)",
            rf"(?:location|ubicaci[oó]n|lugar|place|venue|d[oó]nde|where)[:\s]+\s*({city_word}(?:\s{city_word})*)",
            rf"(?<![A-Za-z])at\s+(?:the\s+)?({city_word}(?:\s{city_word})*)",
            rf"(?:en|in)\s+({city_word}(?:\s{city_word})*)",
            rf"({city_word}(?:\s{city_word})*),\s*({city_word})",
        ]
        seen = set()
        candidates = []
        for pat in patterns:
            for m in re.finditer(pat, text):
                loc = m.group(1).strip().rstrip(",")
                first = loc.split()[0].lower()
                if (loc and loc not in seen and len(loc) > 2
                        and first not in self.FALSE_POSITIVES
                        and len(loc) < 40):
                    seen.add(loc)
                    candidates.append(loc)
        return candidates[0] if candidates else None

    # ------------------------------------------------------------------ #
    # CIUDAD Y PAÍS
    # ------------------------------------------------------------------ #
    def _extract_city_country(self, text, lugar):
        text_lower = text.lower()

        # 1) Buscar país explícito en el texto
        pais = None
        for c in COUNTRIES_ES:
            if c in text_lower:
                pais = c.title()
                break

        # 2) Buscar ciudad conocida en el texto
        ciudad = None
        for city_key, (city_name, country_name) in KNOWN_CITIES.items():
            if city_key in text_lower:
                ciudad = city_name
                if not pais:
                    pais = country_name
                break

        # 3) Si no se encontró ciudad conocida, usar la ubicación extraída
        if not ciudad and lugar:
            lugar_lower = lugar.lower()
            for city_key, (city_name, country_name) in KNOWN_CITIES.items():
                if city_key in lugar_lower:
                    ciudad = city_name
                    if not pais:
                        pais = country_name
                    break

        return ciudad, pais

    # ------------------------------------------------------------------ #
    # TIPO DE LUGAR / VENUE
    # ------------------------------------------------------------------ #
    def _extract_venue_type(self, text):
        text_lower = text.lower()
        found = []
        for vtype, keywords in VENUE_TYPE_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    found.append(vtype)
                    break
        if "festival" in found and len(found) > 1:
            found.remove("festival")
        if "open air" in found and "outdoor" in found:
            found.remove("outdoor")
        return ", ".join(found[:3]) if found else None

    # ------------------------------------------------------------------ #
    # NOMBRE DEL EVENTO
    # ------------------------------------------------------------------ #
    def _extract_name(self, text):
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines:
            if len(line) > 15 and len(line) < 150:
                has_trigger = any(t in line.lower() for t in EVENT_TRIGGERS)
                if has_trigger:
                    clean = re.sub(r"http\S+", "", line).strip()
                    if clean:
                        return clean[:100]
        if lines:
            return lines[0][:100]
        return text[:80].strip()

    # ------------------------------------------------------------------ #
    # ORGANIZADOR
    # ------------------------------------------------------------------ #
    def _extract_organizer(self, text, source_name):
        patterns = [
            # English patterns
            r"(?:organized by|org\s*:|by|presented by|hosted by|promoted by|produced by|curated by|managed by)[:\s]+([A-Z][a-zA-Záéíóúñü\s&.'-]+?)(?:\n|$|\.|,)",
            # Spanish patterns
            r"(?:organizad[ao]\s*por|presentado\s*por|a\s*cargo\s*de|evento\s+de|noche\s+de|party\s+de|fiesta\s+de)[:\s]+([A-Z][a-zA-Záéíóúñü\s&.'-]+?)(?:\n|$|\.|,)",
            # Facebook-specific patterns
            r"(?:Evento de|event by|hosted by|organized by)\s+([A-Z][A-Za-zÀ-ÿ0-9&.' -]{2,60}?)(?:\s+on\s+facebook|\s+·|\s*\||$)",
            # Crew/collective patterns
            r"(?:crew|colectivo|kollektiv|collectif|kollektive|promoter|promotor|collective|kollektiv)[:\s]+([A-Z][a-zA-Záéíóúñü\s&.'-]+?)(?:\n|$|\.|,)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                org = m.group(1).strip().rstrip(".,")
                if len(org) > 2 and not any(skip in org.lower() for skip in ['facebook', 'instagram', 'http', 'twitter']):
                    return org[:60]

        fb_profiles = re.findall(r"facebook\.com/([a-zA-Z0-9.]+)", text)
        if fb_profiles:
            profile = fb_profiles[0]
            if profile not in ('events', 'groups', 'pages', 'login'):
                return profile

        return source_name

    # ------------------------------------------------------------------ #
    # EMAIL
    # ------------------------------------------------------------------ #
    def _extract_email(self, text):
        # Buscar emails en el texto
        m = re.search(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
        if m:
            email = m.group(1)
            # Filtrar emails genéricos de redes sociales
            if not any(skip in email.lower() for skip in ['facebook.com', 'instagram.com', 'twitter.com']):
                return email
        return None

    # ------------------------------------------------------------------ #
    # PRECIO
    # ------------------------------------------------------------------ #
    def _extract_price(self, text):
        patterns = [
            r"(\d+)\s*(?:eur|€|euros)",
            r"(?:eur|€|euros)\s*(\d+)",
            r"(?:precio|price|entry|donation|ticket)[:\s]+(\d+)",
            r"(\d+)\s*(?:usd|\$)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return int(m.group(1))
        return None

    # ------------------------------------------------------------------ #
    # ENLACES
    # ------------------------------------------------------------------ #
    def _extract_links(self, text):
        urls = re.findall(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+", text)
        return [u for u in urls if "facebook.com" not in u.lower()][:5]


def extract_events_from_post(post_text, group_name="", group_url=""):
    extractor = EventExtractor()
    return extractor.extract_all(post_text, group_name, group_url)


if __name__ == "__main__":
    tests = [
        """
        PSYTRANCE FULL MOON PARTY
        Date: 15 de julio de 2026
        Location: Forest, Berlin
        Organized by: Psytrance Collective
        Entry: 25€
        More info: https://example.com
        Contact: info@psytrance.com
        """,
        """
        DARKPSY FESTIVAL 2026
        January 15-17, 2026
        @ Mountain Temple, India
        by Dark Psy Family
        """,
        """
        Next Saturday - Goa Trance Gathering
        en Barcelona, España
        Organizado por Goa Tribe
        donacion 15€
        """,
        """
        OUTDOOR RAVE - Psychedelic Night
        at the Warehouse, Madrid
        Open Air + Camping area
        20€ entry
        organized by Madrid Psy Crew
        """,
    ]

    ex = EventExtractor()
    for t in tests:
        evs = ex.extract_all(t)
        for e in evs:
            print(json.dumps(e, indent=2, ensure_ascii=False))
            print("---")
