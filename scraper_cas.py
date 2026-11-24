#!/usr/bin/env python3
"""
Scraper de convocatorias CAS vigentes (convocatoriasdetrabajo.com) -> SQLite.

Uso:
    pip install requests beautifulsoup4
    python scraper_cas.py --carrera 1 --departamento 15 --nivel 9 --max-paginas 5
    python scraper_cas.py                      # todo el listado CAS vigente
    python scraper_cas.py --sin-detalle        # solo tarjetas del listado (rápido)

Luego consulta con:
    sqlite3 cas.db "SELECT entidad, titulo, sueldo, exp_general_anios, exp_especifica_anios
                    FROM convocatorias
                    WHERE sueldo >= 6000 AND exp_general_anios <= 4
                      AND texto_detalle LIKE '%SIAF%'
                    ORDER BY sueldo DESC;"

Diseño:
- Scraping educado: 1 petición cada --pausa segundos (por defecto 3 s), User-Agent
  identificable, respeta robots.txt (urllib.robotparser) y no vuelve a descargar
  avisos que ya están en la base (incremental).
- El parseo es por TEXTO (etiquetas como "Sueldo", "Vigente hasta", "Experiencia
  General") en lugar de clases CSS, para que resista cambios de maquetación.
- Los años se normalizan a número ("cuatro (04) años" -> 4, "6 meses" -> 0.5).
"""
import argparse
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from urllib import robotparser
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.convocatoriasdetrabajo.com/"
LISTADO = BASE + "convocatorias-cas-vigentes.php"
UA = "cas-buscador-personal/0.1 (uso personal, 1 req/3s)"
AHORA = None
RE_FICHA = re.compile(r"^https?://(www\.)?convocatoriasdetrabajo\.com/oportunidad-laboral-[^/]*-\d+\.html$")  # marca de tiempo común a toda la corrida

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
         "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
NUM_PALABRAS = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
                "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
                "diez": 10, "once": 11, "doce": 12, "quince": 15}


# ---------------------------------------------------------------- utilidades
def sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def a_anios(fragmento: str):
    """'cuatro (04) años' -> 4 ; '2 año(s)' -> 2 ; '6 meses' -> 0.5 ; None si no hay.

    Toma la primera mención del fragmento, sea en número o en palabras."""
    ms = menciones_anios(sin_tildes(fragmento))
    return ms[0][1] if ms else None


def menciones_anios(t: str):
    """[(posición, años)] de cada mención de tiempo en `t` (ya sin tildes y en minúsculas)."""
    pal = "|".join(NUM_PALABRAS)
    out = []
    for m in re.finditer(r"(?:\b(" + pal + r")\b\s*(?:\(\s*(\d+)\s*\)\s*)?|(\d+(?:[.,]\d+)?)\s*\)?\s*)"
                         r"(anos?|ano\(s\)|meses?)\b", t):
        palabra, cifra_par, cifra = m.groups()[:3]
        # "un (04) años": manda la cifra entre paréntesis
        n = float((cifra_par or cifra or "").replace(",", ".") or NUM_PALABRAS[palabra])
        out.append((m.start(), round(n / 12, 2) if m.group(4).startswith("mes") else n))
    return out


def anios_publico(t: str):
    """Años exigidos en el sector público: la mención más cercana antes de "sector público"
    (o la primera después, si la frase es "...en el sector público: 1 año")."""
    t = sin_tildes(t)
    m = re.search(r"sector\s+publico(?![^.\n]{0,15}privad)", t)
    if not m:
        return None
    ms = menciones_anios(t)
    antes = [n for pos, n in ms if pos < m.start()]
    despues = [n for pos, n in ms if pos > m.start()]
    return antes[-1] if antes else (despues[0] if despues else None)


def fecha_iso(txt: str):
    m = re.search(r"(\d{1,2})\s+([a-zA-Z]{3})[a-z]*\.?\s+(\d{4})", txt or "")
    if not m or sin_tildes(m.group(2))[:3] not in MESES:
        return None
    return f"{m.group(3)}-{MESES[sin_tildes(m.group(2))[:3]]:02d}-{int(m.group(1)):02d}"


def dinero(txt: str):
    m = re.search(r"S/\.?\s*([\d.,]+)", txt or "")
    if not m:
        return None
    v = m.group(1).replace(",", "")
    if v.count(".") > 1 or re.search(r"\.\d{3}$", v):  # 8.000 -> 8000
        v = v.replace(".", "")
    try:
        return float(v)
    except ValueError:
        return None


def linea_tras(etiqueta: str, lineas):
    """Devuelve el valor de 'Etiqueta: valor' o la línea siguiente a 'Etiqueta'."""
    et = sin_tildes(etiqueta)
    for i, l in enumerate(lineas):
        ls = sin_tildes(l)
        if ls.startswith(et):
            resto = l[len(etiqueta):].strip(" :")
            return resto if resto else (lineas[i + 1] if i + 1 < len(lineas) else None)
    return None


# ------------------------------------------------------------------ cliente
class Cliente:
    def __init__(self, pausa: float):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.pausa = pausa
        self.ultimo = 0.0
        self.rp = robotparser.RobotFileParser(BASE + "robots.txt")
        try:
            self.rp.read()
        except Exception:
            pass

    def get(self, url: str) -> str:
        if self.rp.default_entry and not self.rp.can_fetch(UA, url):
            raise PermissionError(f"robots.txt no permite {url}")
        espera = self.pausa - (time.time() - self.ultimo)
        if espera > 0:
            time.sleep(espera)
        for intento in range(3):
            r = self.s.get(url, timeout=30)
            self.ultimo = time.time()
            if r.status_code in (429, 503):
                time.sleep(30 * (intento + 1))
                continue
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        r.raise_for_status()
        return r.text


# ---------------------------------------------------------------- listado
# Total que muestra el sitio, p. ej. "Hay 1914 vacante(s) de empleo": cuenta vacantes, no avisos.
RE_TOTAL = re.compile(r"Hay\s+[\d.,]+\s+vacante\(?s?\)?(\s+de\s+empleo(\s+del\s+sector\s+p[uú]blico)?)?", re.I)


def parse_listado(html: str):
    """Extrae las tarjetas: cada enlace a oportunidad-laboral-*.html es un aviso."""
    soup = BeautifulSoup(html, "html.parser")
    vistos, avisos = set(), []
    for a in soup.select('a[href*="oportunidad-laboral-"]'):
        url = urljoin(BASE, a["href"].split("?")[0])
        # Solo fichas del propio sitio: descarta los botones de compartir
        # (LinkedIn, WhatsApp, Messenger...) que llevan la URL del aviso dentro.
        if not RE_FICHA.match(url) or url in vistos:
            continue
        vistos.add(url)
        # Sube hasta el contenedor de la tarjeta (el que incluye "Sueldo")
        card = a
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            if "Sueldo" in card.get_text() and "Vigente" in card.get_text():
                break
        lineas = [l.strip() for l in card.get_text("\n").split("\n") if l.strip()]
        titulo = next((l for l in lineas if l.upper().startswith("CAS N")), None)
        id_m = re.search(r"-(\d+)\.html$", url)
        avisos.append({
            "id": int(id_m.group(1)) if id_m else None,
            "url": url,
            "titulo": titulo,
            "vigente_hasta": fecha_iso(linea_tras("Vigente hasta", lineas) or ""),
            "sueldo": dinero(" ".join(l for l in lineas if "S/" in l)),
            "departamento": linea_tras("Departamento", lineas),
            "vacantes": int(re.sub(r"\D", "", linea_tras("Vacantes", lineas) or "") or 0) or None,
            "requisito_card": next((l for l in lineas if re.match(r"(?i)(t[ií]tul|bachiller|egresad|profesional|estudiante|secundaria)", l)), None),
        })
    siguiente = soup.find("a", string=re.compile(r"(?i)siguiente|»|›"))
    return avisos, (urljoin(LISTADO, siguiente["href"]) if siguiente and siguiente.get("href") else None)


# ---------------------------------------------------------------- detalle
def limpiar_entidad(cabecera):
    """'Convocatoria PODER JUDICIAL CAS N° 024 - ...: PUESTO' -> 'PODER JUDICIAL'."""
    if not cabecera:
        return cabecera
    t = re.sub(r"(?i)^convocatoria\s+", "", cabecera.strip())
    return re.split(r"\s+CAS\s+N", t, maxsplit=1)[0].strip() or cabecera


def elegir_bases(enlaces):
    """De los enlaces oficiales elige el de las bases: PDF con 'bases' > otro PDF > el primero."""
    lista = (enlaces or "").split()
    pdfs = [u for u in lista if re.search(r"\.pdf($|\?)", u, re.I)]
    for grupo in ([u for u in pdfs if re.search(r"(?i)bases|perfil|tdr", u)], pdfs, lista):
        if grupo:
            return grupo[0]
    return None



PATRONES = {
    "exp_general": r"experiencia\s+(?:laboral\s+|profesional\s+)?general[^\n]*",
    "exp_especifica": r"experiencia\s+(?:laboral\s+|profesional\s+)?espec[ií]fica[^\n]*",
    "exp_publico": r"experiencia\s+en\s+el\s+sector\s+p[uú]blico(?![^.\n]{0,15}privad)[^\n]*|"
                   r"de\s+los\s+cuales[^\n]*sector\s+p[uú]blico(?![^.\n]{0,15}privad)[^\n]*",
}

# Secciones de la ficha que vienen después del bloque "Experiencia:"
RE_FIN_BLOQUE = re.compile(r"(?i)^(competencias|cursos|conocimientos?|nota|formaci[oó]n|habilidades|"
                           r"requisitos|otros|descargue|funciones|n[uú]mero de vacantes|publicidad|"
                           r"lugar|remuneraci[oó]n|duraci[oó]n|condiciones|caracter[ií]sticas)\b")


def items_bloque_experiencia(texto):
    """Renglones del bloque "Experiencia:" de la ficha, uniendo los que siguen una frase.

    El sitio suele poner la experiencia como texto libre bajo la etiqueta
    "Experiencia:" (p. ej. "Cuatro (04) años de experiencia laboral...") en vez
    de "Experiencia general: ...". Devuelve [] si la ficha no tiene ese bloque.
    """
    lineas = texto.split("\n")
    ini = next((i for i, l in enumerate(lineas)
                if re.match(r"(?i)^experiencia\s*(laboral\s*)?:?\s*$", l.strip())), None)
    if ini is None:
        return []
    items = []
    for l in lineas[ini + 1: ini + 13]:
        l = l.strip()
        if RE_FIN_BLOQUE.match(l):
            break
        if re.match(r"(?i)^experiencia\s+(laboral\s+)?(general|espec[ií]fica)\s*:?\s*$", l):
            items.append(l)          # subtítulo: el dato viene en la línea siguiente
        elif items and (l[:1].islower() or items[-1].endswith(":") or not re.search(r"[.;)]$", items[-1])) \
                and not re.match(r"(?i)^(general|espec[ií]fica|experiencia|no menor|m[ií]nim|[a-z]\.|\d+\.)", l):
            items[-1] += " " + l     # continuación de la frase anterior
        else:
            items.append(l)
    # Un subtítulo suelto se une con su contenido
    unidos = []
    for it in items:
        if unidos and unidos[-1].endswith(":"):
            unidos[-1] += " " + it
        else:
            unidos.append(it)
    return unidos


def extraer_experiencia(texto):
    """Años y texto de experiencia general, específica y en el sector público."""
    d = {}
    for k, pat in PATRONES.items():
        m = re.search(pat, texto, flags=re.I)
        # a veces el número viene en la línea siguiente a la etiqueta
        frag = texto[m.start(): m.start() + 300] if m else None
        d[k + "_texto"] = frag.split("\n")[0] if frag else None
        n = None
        if frag and k == "exp_publico":
            n = anios_publico(m.group(0))
        elif frag:
            # el número puede ir antes de la etiqueta ("Seis (06) años de experiencia general")
            n = a_anios(texto[texto.rfind("\n", 0, m.start()) + 1: m.end()])
        d[k + "_anios"] = n if n is not None else (a_anios(frag) if frag else None)
    if d["exp_publico_anios"] is None and d["exp_especifica_texto"]:
        m = re.search(r"(?i)de\s+los\s+cuales\s+([^\n]{0,60})sector\s+p[uú]blico", texto)
        if m:
            d["exp_publico_anios"] = a_anios(m.group(1))

    # Respaldo: bloque "Experiencia:" en texto libre. Solo completa lo que falta.
    con_anios = [(it, a_anios(it)) for it in items_bloque_experiencia(texto)]
    con_anios = [(it, n) for it, n in con_anios if n is not None]
    for it, n in con_anios:
        cab = sin_tildes(it[:60])
        if re.search(r"especific", cab):
            k = "exp_especifica"
        elif re.search(r"general", cab) or d["exp_general_anios"] is None:
            k = "exp_general"
        else:
            k = "exp_especifica"
        if d[k + "_anios"] is None:
            d[k + "_anios"], d[k + "_texto"] = n, it
        if d["exp_publico_anios"] is None and anios_publico(it) is not None:
            d["exp_publico_anios"], d["exp_publico_texto"] = anios_publico(it), it
    return d


def parse_detalle(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header"]):
        t.decompose()
    texto = "\n".join(l.strip() for l in soup.get_text("\n").split("\n") if l.strip())
    d = {"texto_detalle": texto, **extraer_experiencia(texto)}
    m = re.search(r"(?i)(t[ií]tulo|bachiller|egresad[oa])[^\n]{10,300}", texto)
    d["formacion"] = m.group(0) if m else None
    d["cursos"] = " | ".join(re.findall(r"(?im)^.*(?:curso|diplomado|especializaci[oó]n|programa de)[^\n]{5,250}$", texto)[:6]) or None
    cab = soup.find("h1") or soup.find("title")
    d["entidad"] = limpiar_entidad(cab.get_text(" ", strip=True)) if cab else None
    enlaces = [a["href"] for a in soup.find_all("a", href=True)
               if re.search(r"\.gob\.pe|\.pdf$|bases", a["href"], re.I)]
    d["enlaces_oficiales"] = " ".join(dict.fromkeys(enlaces)) or None
    d["enlace_bases"] = elegir_bases(d["enlaces_oficiales"])
    return d


# ------------------------------------------------------------------- base
ESQUEMA = """
CREATE TABLE IF NOT EXISTS convocatorias (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE, titulo TEXT, entidad TEXT,
  departamento TEXT, sueldo REAL, vacantes INTEGER, vigente_hasta TEXT,
  requisito_card TEXT, formacion TEXT,
  exp_general_anios REAL, exp_general_texto TEXT,
  exp_especifica_anios REAL, exp_especifica_texto TEXT,
  exp_publico_anios REAL, exp_publico_texto TEXT,
  cursos TEXT, enlace_bases TEXT, enlaces_oficiales TEXT,
  texto_detalle TEXT, filtros TEXT, visto_en TEXT, primer_visto TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS busqueda USING fts5(
  titulo, entidad, formacion, exp_especifica_texto, cursos, texto_detalle,
  content='convocatorias', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
"""


def migrar(db):
    """Añade a una base antigua las columnas que le falten (p. ej. primer_visto)."""
    cols = {c[1] for c in db.execute("PRAGMA table_info(convocatorias)")}
    for nombre, tipo in re.findall(r"(\w+) (TEXT|REAL|INTEGER)", ESQUEMA.split("CREATE VIRTUAL")[0]):
        if nombre not in cols:
            db.execute(f"ALTER TABLE convocatorias ADD COLUMN {nombre} {tipo}")
            cols.add(nombre)
    db.execute("UPDATE convocatorias SET primer_visto = visto_en WHERE primer_visto IS NULL")
    # Recalcula entidad y enlace_bases con las reglas actuales
    for id_, ent, enl in db.execute("SELECT id, entidad, enlaces_oficiales FROM convocatorias").fetchall():
        db.execute("UPDATE convocatorias SET entidad=?, enlace_bases=COALESCE(?, enlace_bases) WHERE id=?",
                   (limpiar_entidad(ent), elegir_bases(enl), id_))
    # Recalcula la experiencia desde el texto guardado de cada ficha (reglas actuales)
    for id_, texto in db.execute("SELECT id, texto_detalle FROM convocatorias "
                                 "WHERE texto_detalle IS NOT NULL").fetchall():
        e = extraer_experiencia(texto)
        db.execute(f"UPDATE convocatorias SET {', '.join(k + '=?' for k in e)} WHERE id=?",
                   [*e.values(), id_])
    # Limpia filas basura de versiones anteriores (botones de compartir sin id)
    db.execute("DELETE FROM convocatorias WHERE id IS NULL OR url NOT LIKE '%convocatoriasdetrabajo.com/oportunidad-laboral-%'")


def guardar(db, fila):
    """Inserta o actualiza SOLO las columnas presentes en `fila`.

    Así, un aviso ya conocido que hoy solo trae datos del listado conserva su
    detalle (texto_detalle, experiencia, etc.) y su fecha `primer_visto`.
    """
    cols = [c[1] for c in db.execute("PRAGMA table_info(convocatorias)")]
    fila = {k: v for k, v in fila.items() if k in cols and k != "primer_visto"}
    fila["primer_visto"] = fila.get("visto_en")
    nombres = list(fila)
    actualiza = ", ".join(f"{c}=COALESCE(excluded.{c}, {c})" for c in nombres
                          if c not in ("id", "primer_visto"))
    db.execute(f"INSERT INTO convocatorias ({','.join(nombres)}) VALUES ({','.join('?' * len(nombres))}) "
               f"ON CONFLICT(id) DO UPDATE SET {actualiza}", list(fila.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--carrera"); ap.add_argument("--departamento"); ap.add_argument("--nivel")
    ap.add_argument("--max-paginas", type=int, default=200)
    ap.add_argument("--pausa", type=float, default=3.0)
    ap.add_argument("--db", default="cas.db")
    ap.add_argument("--sin-detalle", action="store_true")
    a = ap.parse_args()
    global AHORA
    AHORA = datetime.now().isoformat(timespec="seconds")

    params = {k: v for k, v in {"carrera": a.carrera, "departamento": a.departamento,
                                "nivel_estudios": a.nivel, "sort": "1-fechapublicacion"}.items() if v}
    cli, db = Cliente(a.pausa), sqlite3.connect(a.db)
    db.executescript(ESQUEMA)
    migrar(db)
    ya = {r[0] for r in db.execute("SELECT url FROM convocatorias WHERE texto_detalle IS NOT NULL")}

    url, pagina, total = f"{LISTADO}?{urlencode(params)}", 1, 0
    vistos_corrida, repetidas = set(), 0
    while url and pagina <= a.max_paginas:
        print(f"[listado] página {pagina}: {url}", file=sys.stderr)
        html = cli.get(url)
        if pagina == 1:
            m = RE_TOTAL.search(BeautifulSoup(html, "html.parser").get_text(" "))
            print(f"[listado] el sitio anuncia: {m.group(0) if m else 'sin total visible'}", file=sys.stderr)
        avisos, sig = parse_listado(html)
        nuevos_pag = [av for av in avisos if av["url"] not in vistos_corrida]
        print(f"   {len(avisos)} avisos, {len(nuevos_pag)} no vistos antes en esta corrida", file=sys.stderr)
        if not avisos:
            break
        # Fin del listado: el sitio repite la última página. Una sola página repetida
        # puede ser un tropiezo del sitio, así que se corta tras 3 seguidas sin nada nuevo.
        if not nuevos_pag:
            repetidas += 1
            if repetidas >= 3:
                break
            pagina += 1
            url = f"{LISTADO}?{urlencode({**params, 'page': pagina})}"
            continue
        repetidas = 0
        vistos_corrida.update(av["url"] for av in avisos)
        avisos = nuevos_pag
        for av in avisos:
            av["filtros"] = urlencode(params)
            av["visto_en"] = AHORA
            if not a.sin_detalle and av["url"] not in ya:
                try:
                    av.update(parse_detalle(cli.get(av["url"])))
                except Exception as e:  # noqa
                    print(f"  ! {av['url']}: {e}", file=sys.stderr)
            guardar(db, av)
            total += 1
        db.commit()
        pagina += 1
        url = sig or f"{LISTADO}?{urlencode({**params, 'page': pagina})}"
    db.execute("INSERT INTO busqueda(busqueda) VALUES('rebuild')")
    db.commit()
    print(f"Listo: {total} avisos procesados en {a.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
