#!/usr/bin/env python3
"""
Fuente de datos para Looker Studio (vía Google Sheets con IMPORTDATA).

Lee la base histórica cas.db y los CSV diarios de data/ y escribe, siempre con
el mismo nombre (URL estable):

  data/looker/convocatorias.csv   una fila por convocatoria vista alguna vez,
                                  con campos limpios y derivados para graficar
  data/looker/resumen_diario.csv  una fila por día de corrida: vigentes y nuevas

No modifica cas.db ni los CSV diarios. Formato pensado para Sheets/Looker:
UTF-8 sin BOM, punto decimal, fechas AAAA-MM-DD y textos en una sola línea.

Uso:  python looker.py [--db cas.db]
"""
import argparse
import csv
import glob
import os
import re
import sqlite3
import statistics
import unicodedata

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(AQUI, "data", "looker")

# Nombre oficial del departamento -> código ISO 3166-2 (lo entiende el mapa de Looker)
REGIONES = {
    "Amazonas": "PE-AMA", "Áncash": "PE-ANC", "Apurímac": "PE-APU", "Arequipa": "PE-ARE",
    "Ayacucho": "PE-AYA", "Cajamarca": "PE-CAJ", "Callao": "PE-CAL", "Cusco": "PE-CUS",
    "Huancavelica": "PE-HUV", "Huánuco": "PE-HUC", "Ica": "PE-ICA", "Junín": "PE-JUN",
    "La Libertad": "PE-LAL", "Lambayeque": "PE-LAM", "Lima": "PE-LIM", "Loreto": "PE-LOR",
    "Madre de Dios": "PE-MDD", "Moquegua": "PE-MOQ", "Pasco": "PE-PAS", "Piura": "PE-PIU",
    "Puno": "PE-PUN", "San Martín": "PE-SAM", "Tacna": "PE-TAC", "Tumbes": "PE-TUM",
    "Ucayali": "PE-UCA",
}

# (patrón sobre el nombre de la entidad sin tildes y en mayúsculas, tipo) — gana el primero
TIPOS_ENTIDAD = [
    (r"\bUGEL|\bDRE\b|DIRECCION REGIONAL DE EDUCACION|GERENCIA REGIONAL DE EDUCACION", "Educación (UGEL/DRE)"),
    (r"HOSPITAL|\bRED (DE SALUD|INTEGRADA)|\bDIRESA\b|\bDIRIS\b|\bGERESA\b|SALUD|ESSALUD|\bINSN?\b|INSTITUTO NACIONAL DE (SALUD|ENFERMEDADES)|^IRO\b", "Salud"),
    (r"UNIVERSIDAD|^UN[A-Z]{2,}\b", "Universidad"),
    (r"MUNICIPALIDAD", "Municipalidad"),
    (r"GOBIERNO REGIONAL|^GORE|GERENCIA SUB ?REGIONAL|DIRECCION (DE AGRICULTURA|AGRARIA)|DIRECCION REGIONAL", "Gobierno regional"),
    (r"PODER JUDICIAL|CORTE SUPERIOR|MINISTERIO PUBLICO|FISCALIA|\bJNJ\b|TRIBUNAL CONSTITUCIONAL|PROCURADURIA", "Justicia"),
    (r"MINISTERIO", "Ministerio"),
    (r"PROGRAMA|JUNTOS|PRONABEC|PRONIED|INABIF|QALI WARMA|WASI|CUNA MAS|PENSION 65|FONCODES|AURORA", "Programa social o nacional"),
    (r"^SUNA|^SU[A-Z]{3,}\b|^OS[A-Z]{3,}\b|INDECOPI|OEFA|MIGRACIONES|SERVIR|SENAMHI|SERNANP|INDECI|SENASA|\bINEI\b|\bONPE\b|\bJNE\b|RENIEC|CONTRALORIA|DEFENSORIA|\bATU\b|SUTRAN|\bIRTP\b|PROMPERU|FONDEPES|SENACE|\bOTASS\b|\bANA\b|CONCYTEC|DEVIDA|\bINGEMMET\b|\bINPE\b|\bPNP\b|\bIPD\b|SANIPES|CONADIS|PROVIAS|BIBLIOTECA NACIONAL|\bAMAG\b", "Organismo especializado"),
]

COLUMNAS = ["id", "entidad", "tipo_entidad", "titulo", "departamento", "region_iso",
            "sueldo", "rango_sueldo", "vacantes", "vigente_hasta", "nivel_formacion",
            "requisito_card", "formacion", "cursos", "exp_especifica_texto",
            "exp_general_anios", "exp_especifica_anios", "exp_publico_anios", "rango_experiencia",
            "fecha_publicacion", "ultima_vez_vista", "vigente", "nueva", "tiene_bases", "url", "enlace_bases"]


def sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


_CLAVES_REGION = {sin_tildes(k).upper(): k for k in REGIONES}


def normalizar_departamento(d):
    d = (d or "").strip()
    return _CLAVES_REGION.get(sin_tildes(d).upper(), d.title() if d else "Sin dato")


def tipo_entidad(entidad):
    e = sin_tildes(entidad).upper()
    for patron, tipo in TIPOS_ENTIDAD:
        if re.search(patron, e):
            return tipo
    return "Otro"


def nivel_formacion(texto):
    t = sin_tildes(texto).lower()
    if not t.strip():
        return "Sin dato"
    if re.search(r"maestr|doctor|especialista en medicina|segunda especialidad|residentado", t):
        return "Posgrado / especialidad"
    if re.search(r"titul\w* (profesional )?(universitari|pedagog)|licenciad|titulo profesional(?! tecnico)|abogad|medico cirujano|colegiatura", t):
        return "Título universitario"
    if re.search(r"bachiller", t):
        return "Bachiller"
    if re.search(r"egresad|estudiante|estudios universitari", t):
        return "Egresado / estudiante"
    if re.search(r"tecnic|instituto", t):
        return "Técnico"
    if re.search(r"secundaria|primaria", t):
        return "Secundaria"
    return "Otro"


def rango_sueldo(s):
    if s is None:
        return "Sin dato"
    for tope, etiqueta in [(1500, "1) Hasta 1 500"), (2500, "2) 1 501–2 500"), (4000, "3) 2 501–4 000"),
                           (6000, "4) 4 001–6 000"), (9000, "5) 6 001–9 000")]:
        if s <= tope:
            return etiqueta
    return "6) Más de 9 000"


def rango_experiencia(a):
    if a is None:
        return "Sin dato"
    if a < 1:
        return "1) Menos de 1 año"
    if a <= 2:
        return "2) 1–2 años"
    if a <= 4:
        return "3) 3–4 años"
    return "4) 5 años o más"


def num(v):
    """Número con punto decimal y sin '.0' sobrante; vacío si no hay dato."""
    if v is None or v == "":
        return ""
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def texto(v):
    """Texto en una sola línea (saltos y espacios repetidos -> un espacio)."""
    return " ".join((v or "").split())


def escribir(ruta, columnas, filas):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, columnas, lineterminator="\n")
        w.writeheader()
        w.writerows(filas)


def convocatorias(db):
    db.row_factory = sqlite3.Row
    ultima = db.execute("SELECT max(substr(visto_en,1,10)) FROM convocatorias").fetchone()[0] or ""
    filas = []
    for c in db.execute("SELECT * FROM convocatorias ORDER BY id DESC"):
        depto = normalizar_departamento(c["departamento"])
        visto = (c["visto_en"] or "")[:10]
        filas.append({
            "id": c["id"],
            "entidad": (c["entidad"] or "").strip(),
            "tipo_entidad": tipo_entidad(c["entidad"]),
            "titulo": (c["titulo"] or "").strip(),
            "departamento": depto,
            "region_iso": REGIONES.get(depto, ""),
            "sueldo": num(c["sueldo"]),
            "rango_sueldo": rango_sueldo(c["sueldo"]),
            "vacantes": num(c["vacantes"]),
            "vigente_hasta": c["vigente_hasta"] or "",
            "nivel_formacion": nivel_formacion(c["formacion"] or c["requisito_card"]),
            "requisito_card": texto(c["requisito_card"]),
            "formacion": texto(c["formacion"]),
            "cursos": texto(c["cursos"]),
            "exp_especifica_texto": texto(c["exp_especifica_texto"]),
            "exp_general_anios": num(c["exp_general_anios"]),
            "exp_especifica_anios": num(c["exp_especifica_anios"]),
            "exp_publico_anios": num(c["exp_publico_anios"]),
            # si solo piden experiencia específica, esa es también el mínimo de experiencia general
            "rango_experiencia": rango_experiencia(c["exp_general_anios"] if c["exp_general_anios"] is not None
                                                   else c["exp_especifica_anios"]),
            "fecha_publicacion": (c["primer_visto"] or "")[:10],
            "ultima_vez_vista": visto,
            # sigue publicada en la última corrida y su plazo no venció
            "vigente": "sí" if visto == ultima and (c["vigente_hasta"] or "9999") >= ultima else "no",
            # apareció por primera vez en la última corrida
            "nueva": "sí" if (c["primer_visto"] or "")[:10] == ultima else "no",
            "tiene_bases": "sí" if c["enlace_bases"] else "no",
            "url": c["url"] or "",
            "enlace_bases": c["enlace_bases"] or "",
        })
    return filas


def resumen_diario():
    filas = []
    for ruta in sorted(glob.glob(os.path.join(AQUI, "data", "????-??-??.csv"))):
        with open(ruta, encoding="utf-8-sig", newline="") as f:
            rs = list(csv.DictReader(f))
        sueldos = [float(r["sueldo"]) for r in rs if r.get("sueldo")]
        filas.append({
            "fecha": os.path.basename(ruta)[:10],
            "convocatorias_vistas": len(rs),
            "nuevas": sum(1 for r in rs if r.get("nueva_hoy") == "sí"),
            "vacantes": sum(int(float(r["vacantes"])) for r in rs if r.get("vacantes")),
            "sueldo_mediano": num(statistics.median(sueldos)) if sueldos else "",
        })
    return filas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(AQUI, "cas.db"))
    a = ap.parse_args()
    filas = convocatorias(sqlite3.connect(a.db))
    escribir(os.path.join(SALIDA, "convocatorias.csv"), COLUMNAS, filas)
    dias = resumen_diario()
    escribir(os.path.join(SALIDA, "resumen_diario.csv"),
             ["fecha", "convocatorias_vistas", "nuevas", "vacantes", "sueldo_mediano"], dias)
    print(f"Looker: {len(filas)} convocatorias, {len(dias)} días -> {SALIDA}")


if __name__ == "__main__":
    main()
