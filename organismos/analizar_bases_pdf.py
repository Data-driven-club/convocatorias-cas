#!/usr/bin/env python3
"""
Paso 2 - Bajar las bases (PDF) de cada convocatoria y diagnosticar cómo extraerlas.

Lee fuentes_detectadas.csv (del paso 1), descarga los enlaces de rol bases/perfil
que sean PDF (directos, cdn.www.gob.pe o Google Drive) y para cada uno reporta:
  - si tiene texto o es escaneado (necesita OCR)
  - si sigue el formato de perfil de puesto de SERVIR (DL 1057)
  - experiencia general / específica / sector público encontrada por reglas
  - número de páginas y de perfiles (puestos) detectados

Uso:
  pip install requests pdfplumber
  python analizar_bases_pdf.py --max 200
Salidas: pdfs/ (descargas) y diagnostico_pdfs.csv

Para los escaneados: ocrmypdf (pip install ocrmypdf; requiere tesseract-ocr con
idioma 'spa') convierte el PDF a PDF con texto y luego se vuelve a correr este script.
"""
import argparse
import csv
import hashlib
import os
import re
import sys
import time
from urllib import robotparser
from urllib.parse import parse_qs, unquote, urlparse

import pdfplumber
import requests

UA = "cas-catalogo-personal/0.1 (uso personal, 1 req/3s)"
NUM = {"un": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
       "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def url_descarga(url):
    u = urlparse(url)
    if "docs.google.com" in u.netloc and "viewer" in u.path:
        return unquote(parse_qs(u.query).get("url", [url])[0])
    m = re.search(r"drive\.google\.com/file/d/([^/]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def anios(txt):
    t = txt.lower()
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*\)?\s*(años?|año\(s\)|meses?)", t)
    if not m:
        m2 = re.search(r"\b(" + "|".join(NUM) + r")\b\s*(?:\(\d+\)\s*)?(años?|año\(s\)|meses?)", t)
        if not m2:
            return None
        n, u = NUM[m2.group(1)], m2.group(2)
    else:
        n, u = float(m.group(1).replace(",", ".")), m.group(2)
    return round(n / 12, 2) if u.startswith("mes") else float(n)


def diagnosticar(ruta):
    d = {"paginas": 0, "chars_por_pagina": 0, "necesita_ocr": "", "formato_servir": "",
         "perfiles_detectados": 0, "exp_general": "", "exp_especifica": "", "exp_publico": ""}
    with pdfplumber.open(ruta) as pdf:
        d["paginas"] = len(pdf.pages)
        textos = [(p.extract_text() or "") for p in pdf.pages]
    texto = "\n".join(textos)
    d["chars_por_pagina"] = len(texto) // max(1, d["paginas"])
    d["necesita_ocr"] = "sí" if d["chars_por_pagina"] < 200 else "no"
    tl = texto.lower()
    marcas = ["perfil del puesto", "formación académica", "experiencia laboral general",
              "experiencia laboral específica", "conocimientos", "decreto legislativo n° 1057"]
    d["formato_servir"] = f"{sum(m in tl for m in marcas)}/{len(marcas)}"
    d["perfiles_detectados"] = len(re.findall(r"(?i)perfil\s+del\s+puesto|identificaci[oó]n\s+del\s+puesto", texto))
    for clave, pat in [("exp_general", r"experiencia\s+(?:laboral\s+)?general[^\n]{0,160}"),
                       ("exp_especifica", r"experiencia\s+(?:laboral\s+)?espec[ií]fica[^\n]{0,200}"),
                       ("exp_publico", r"sector\s+p[uú]blico[^\n]{0,120}")]:
        m = re.search(pat, texto, re.I)
        if m:
            # en las tablas SERVIR el número suele caer en la línea siguiente
            trozo = texto[m.start(): m.start() + 260]
            d[clave] = anios(trozo) if anios(trozo) is not None else ""
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuentes", default="fuentes_detectadas.csv")
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--pausa", type=float, default=3.0)
    a = ap.parse_args()

    os.makedirs("pdfs", exist_ok=True)
    s = requests.Session(); s.headers["User-Agent"] = UA
    robots, ult = {}, 0.0
    filas = [r for r in csv.DictReader(open(a.fuentes, encoding="utf-8-sig"))
             if r["rol"] in ("bases", "perfil") and r["tipo"] in ("PDF directo", "gob.pe (PDF)", "Google Drive")]
    vistos, salida = set(), []
    for r in filas[: a.max]:
        url = url_descarga(r["url"])
        if url in vistos:
            continue
        vistos.add(url)
        host = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if host not in robots:
            rp = robotparser.RobotFileParser(host + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None
            robots[host] = rp
        if robots[host] and not robots[host].can_fetch(UA, url):
            salida.append({**r, "estado": "bloqueado por robots.txt"}); continue
        time.sleep(max(0, a.pausa - (time.time() - ult)))
        try:
            resp = s.get(url, timeout=60); ult = time.time(); resp.raise_for_status()
            if not resp.content.startswith(b"%PDF"):
                salida.append({**r, "estado": "no es PDF (HTML o descarga protegida)"}); continue
            ruta = os.path.join("pdfs", hashlib.md5(url.encode()).hexdigest()[:12] + ".pdf")
            open(ruta, "wb").write(resp.content)
            salida.append({**r, "estado": "ok", "archivo": ruta, **diagnosticar(ruta)})
        except Exception as ex:
            salida.append({**r, "estado": f"error: {ex}"[:120]})
        print(f"{len(salida)} {r['entidad']}: {salida[-1]['estado']}", file=sys.stderr)

    cols = ["entidad", "url", "tipo", "estado", "archivo", "paginas", "chars_por_pagina", "necesita_ocr",
            "formato_servir", "perfiles_detectados", "exp_general", "exp_especifica", "exp_publico"]
    with open("diagnostico_pdfs.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore"); w.writeheader(); w.writerows(salida)
    print("Listo: diagnostico_pdfs.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
