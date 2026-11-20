#!/usr/bin/env python3
"""
Corrida diaria: scraping incremental + CSV del día + informe Markdown.

Pasos:
  1. Ejecuta scraper_cas.py sobre la base SQLite histórica (cas.db).
  2. Escribe data/AAAA-MM-DD.csv con todas las convocatorias vistas hoy
     (sin el texto completo de la ficha, para que el CSV sea liviano).
  3. Para las convocatorias NUEVAS de hoy descarga sus bases si son PDF,
     las analiza en memoria (no se guardan los PDF) y guarda el resultado
     en la tabla `bases_pdf` de cas.db.
  4. Escribe informes/AAAA-MM-DD.md con las nuevas y el resumen de cada PDF.

Uso (a mano):
  pip install -r requirements.txt
  python diario.py                                # listado CAS completo
  python diario.py --departamento 15 --max-paginas 5
  python diario.py --sin-scraping                 # rehace CSV/informe con la base actual
"""
import argparse
import csv
import io
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "organismos"))
from analizar_bases_pdf import UA, diagnosticar, url_descarga  # noqa: E402

AQUI = os.path.dirname(os.path.abspath(__file__))

COLUMNAS_CSV = ["id", "entidad", "titulo", "departamento", "sueldo", "vacantes", "vigente_hasta",
                "requisito_card", "formacion", "exp_general_anios", "exp_especifica_anios",
                "exp_publico_anios", "exp_especifica_texto", "cursos", "enlace_bases", "url",
                "primer_visto", "nueva_hoy"]

ESQUEMA_PDF = """
CREATE TABLE IF NOT EXISTS bases_pdf (
  url_bases TEXT PRIMARY KEY, convocatoria_id INTEGER, estado TEXT, analizado_en TEXT,
  paginas INTEGER, chars_por_pagina INTEGER, necesita_ocr TEXT, formato_servir TEXT,
  perfiles_detectados INTEGER, exp_general TEXT, exp_especifica TEXT, exp_publico TEXT
);
"""


def correr_scraper(a):
    cmd = [sys.executable, os.path.join(AQUI, "scraper_cas.py"), "--db", a.db,
           "--max-paginas", str(a.max_paginas), "--pausa", str(a.pausa)]
    for k in ("carrera", "departamento", "nivel"):
        if getattr(a, k):
            cmd += [f"--{k}", getattr(a, k)]
    print("$", " ".join(cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def parece_pdf(url):
    u = (url or "").lower()
    return u.endswith(".pdf") or "drive.google.com/file/d/" in u or "docs.google.com/viewer" in u \
        or "descargar" in u


def analizar_pdfs(db, nuevas, max_pdfs, pausa):
    db.executescript(ESQUEMA_PDF)
    hechos = {r[0] for r in db.execute("SELECT url_bases FROM bases_pdf")}
    s = requests.Session()
    s.headers["User-Agent"] = UA
    n, ult = 0, 0.0
    for c in nuevas:
        url = c["enlace_bases"]
        if not url or url in hechos or not parece_pdf(url) or n >= max_pdfs:
            continue
        n += 1
        hechos.add(url)  # varias convocatorias pueden compartir las mismas bases
        fila = {"url_bases": url, "convocatoria_id": c["id"],
                "analizado_en": datetime.now().isoformat(timespec="seconds")}
        time.sleep(max(0, pausa - (time.time() - ult)))
        try:
            r = s.get(url_descarga(url), timeout=60)
            ult = time.time()
            r.raise_for_status()
            if not r.content.startswith(b"%PDF"):
                fila["estado"] = "no es PDF (HTML o descarga protegida)"
            else:
                fila.update(estado="ok", **diagnosticar(io.BytesIO(r.content)))
        except Exception as ex:  # noqa
            fila["estado"] = f"error: {ex}"[:120]
        print(f"  [pdf {n}] {urlparse(url).netloc}: {fila['estado']}", file=sys.stderr)
        db.execute(f"INSERT OR REPLACE INTO bases_pdf ({','.join(fila)}) VALUES ({','.join('?' * len(fila))})",
                   list(fila.values()))
        db.commit()


def fmt(v, suf=""):
    if v is None or v == "":
        return "—"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v}{suf}"


def escribir_informe(ruta, hoy, vigentes, nuevas, pdfs):
    L = [f"# Convocatorias CAS — {hoy}", "",
         f"- Convocatorias vistas hoy: **{len(vigentes)}**",
         f"- Nuevas desde la última corrida: **{len(nuevas)}**",
         f"- Bases PDF analizadas: **{sum(1 for p in pdfs.values() if p['estado'] == 'ok')}**"
         f" de {len(pdfs)} intentadas", ""]
    if not nuevas:
        L.append("No hay convocatorias nuevas hoy.")
    else:
        L += ["## Nuevas convocatorias", "",
              "| Entidad | Puesto | Sueldo | Vigente hasta | Exp. gral / esp. (años) | Depto. |",
              "|---|---|---:|---|---|---|"]
        for c in sorted(nuevas, key=lambda c: -(c["sueldo"] or 0)):
            titulo = (c["titulo"] or "").replace("|", "/")
            L.append(f"| {(c['entidad'] or '—').replace('|', '/')} | [{titulo or 'ver aviso'}]({c['url']}) "
                     f"| {fmt(c['sueldo'])} | {fmt(c['vigente_hasta'])} "
                     f"| {fmt(c['exp_general_anios'])} / {fmt(c['exp_especifica_anios'])} "
                     f"| {fmt(c['departamento'])} |")
        L += ["", "## Resumen de las bases (PDF)", ""]
        con_pdf = [c for c in nuevas if c["enlace_bases"] in pdfs]
        if not con_pdf:
            L.append("Ninguna convocatoria nueva tenía bases en PDF descargable.")
        for c in con_pdf:
            p = pdfs[c["enlace_bases"]]
            L += [f"### {c['entidad'] or '—'} — {c['titulo'] or c['url']}", "",
                  f"- Bases: <{c['enlace_bases']}>", f"- Estado: {p['estado']}"]
            if p["estado"] == "ok":
                L += [f"- Páginas: {fmt(p['paginas'])} · perfiles detectados: {fmt(p['perfiles_detectados'])}"
                      f" · formato SERVIR: {fmt(p['formato_servir'])}"
                      f" · necesita OCR: {fmt(p['necesita_ocr'])}",
                      f"- Experiencia en el PDF (años): general {fmt(p['exp_general'])}, "
                      f"específica {fmt(p['exp_especifica'])}, sector público {fmt(p['exp_publico'])}"]
            L.append("")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(L).rstrip() + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--carrera"); ap.add_argument("--departamento"); ap.add_argument("--nivel")
    ap.add_argument("--max-paginas", type=int, default=50)
    ap.add_argument("--pausa", type=float, default=3.0)
    ap.add_argument("--max-pdfs", type=int, default=40, help="PDF de bases a analizar por día")
    ap.add_argument("--db", default="cas.db")
    ap.add_argument("--fecha", default=date.today().isoformat(), help="AAAA-MM-DD de la corrida")
    ap.add_argument("--sin-scraping", action="store_true")
    a = ap.parse_args()

    if not a.sin_scraping:
        correr_scraper(a)

    db = sqlite3.connect(a.db)
    db.row_factory = sqlite3.Row
    hoy = a.fecha
    vigentes = [dict(r) for r in db.execute(
        "SELECT * FROM convocatorias WHERE substr(visto_en,1,10)=? ORDER BY id DESC", (hoy,))]
    for c in vigentes:
        c["nueva_hoy"] = "sí" if (c.get("primer_visto") or "")[:10] == hoy else "no"
    nuevas = [c for c in vigentes if c["nueva_hoy"] == "sí"]

    os.makedirs(os.path.join(AQUI, "data"), exist_ok=True)
    ruta_csv = os.path.join(AQUI, "data", f"{hoy}.csv")
    with open(ruta_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLUMNAS_CSV, extrasaction="ignore")
        w.writeheader()
        w.writerows(vigentes)

    # primero las bases de las nuevas; con el cupo que sobre, las vigentes aún sin analizar
    analizar_pdfs(db, nuevas + [c for c in vigentes if c["nueva_hoy"] == "no"], a.max_pdfs, a.pausa)
    pdfs = {r["url_bases"]: dict(r) for r in db.execute(
        "SELECT * FROM bases_pdf WHERE url_bases IN (%s)" % ",".join("?" * len(nuevas)),
        [c["enlace_bases"] for c in nuevas])} if nuevas else {}

    ruta_md = os.path.join(AQUI, "informes", f"{hoy}.md")
    escribir_informe(ruta_md, hoy, vigentes, nuevas, pdfs)
    print(f"Listo: {len(vigentes)} vistas, {len(nuevas)} nuevas -> {ruta_csv}, {ruta_md}", file=sys.stderr)


if __name__ == "__main__":
    main()
