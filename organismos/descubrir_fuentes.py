#!/usr/bin/env python3
"""
Paso 1 - Descubrir las fuentes oficiales de cada organismo.

Recorre el directorio de organismos de convocatoriasdetrabajo.com, abre hasta
--avisos-por-entidad fichas de cada organismo y guarda todos los enlaces que salen
del sitio (bases, postulación, comunicados, resultados), clasificados por tipo.

Salidas:
  fuentes_detectadas.csv   una fila por enlace oficial encontrado
  resumen_por_entidad.csv  una fila por organismo: dominio oficial, tipo dominante,
                           ejemplo de bases, si el enlace es PDF directo, etc.

Uso:
  pip install requests beautifulsoup4
  python descubrir_fuentes.py --catalogo catalogo_organismos.csv --avisos-por-entidad 3
  python descubrir_fuentes.py --solo-cas --solo-pendientes     # solo las no analizadas

Scraping educado: 1 petición cada --pausa s (3 por defecto) y respeta robots.txt.
"""
import argparse
import csv
import re
import sys
import time
from collections import Counter, defaultdict
from urllib import robotparser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.convocatoriasdetrabajo.com/"
UA = "cas-catalogo-personal/0.1 (uso personal, 1 req/3s)"


class Cliente:
    def __init__(self, pausa):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.pausa, self.t = pausa, 0.0
        self.robots = {}

    def permitido(self, url):
        host = urlparse(url).scheme + "://" + urlparse(url).netloc
        if host not in self.robots:
            rp = robotparser.RobotFileParser(host + "/robots.txt")
            try:
                rp.read()
            except Exception:
                rp = None
            self.robots[host] = rp
        rp = self.robots[host]
        return rp is None or rp.can_fetch(UA, url)

    def get(self, url, **kw):
        if not self.permitido(url):
            raise PermissionError("robots.txt no lo permite")
        espera = self.pausa - (time.time() - self.t)
        if espera > 0:
            time.sleep(espera)
        r = self.s.get(url, timeout=40, **kw)
        self.t = time.time()
        r.raise_for_status()
        return r


def clasificar(url, etiqueta=""):
    u = urlparse(url)
    host, path = u.netloc.lower(), u.path.lower()
    if "docs.google.com" in host and "viewer" in path:           # visor que envuelve un PDF
        real = parse_qs(u.query).get("url", [""])[0]
        if real:
            return clasificar(unquote(real), etiqueta)
    if host.endswith("gob.pe") and (host == "www.gob.pe" or host.startswith("cdn.www.gob.pe")):
        return "gob.pe (PDF)" if path.endswith(".pdf") else "gob.pe (página)"
    if "drive.google.com" in host or ("docs.google.com" in host and "/forms" not in path):
        return "Google Drive"
    if "forms.gle" in host or "/forms" in path:
        return "Google Forms"
    if "facebook.com" in host:
        return "Facebook"
    if "youtube.com" in host or path.endswith((".mp4",)):
        return "Video"
    if path.endswith(".pdf"):
        return "PDF directo"
    if path.endswith((".doc", ".docx", ".xls", ".xlsx")):
        return "Anexo editable"
    if re.search(r"\.(aspx|xhtml|jsf|do)$", path) or re.search(r"psep|sigepo|postula|login|extranet|siscon|/jobs", path + " " + host):
        return "Portal de postulación"
    return "Web propia"


def rol(etiqueta):
    e = etiqueta.lower()
    for k, v in [("base", "bases"), ("perfil", "perfil"), ("anexo", "anexo"), ("postul", "postulación"),
                 ("resultado", "resultados"), ("comunicado", "comunicados"), ("cronograma", "cronograma")]:
        if k in e:
            return v
    return "otro"


def fichas_de_entidad(cli, url_entidad, n, solo_cas):
    html = cli.get(url_entidad).text
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.select('a[href*="oportunidad-laboral-"]'):
        u = urljoin(BASE, a["href"].split("?")[0])
        if u in urls or (solo_cas and "-cas-" not in u):
            continue
        urls.append(u)
        if len(urls) >= n:
            break
    return urls


def enlaces_oficiales(cli, url_ficha):
    soup = BeautifulSoup(cli.get(url_ficha).text, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http") or "convocatoriasdetrabajo.com" in href:
            continue
        if re.search(r"facebook\.com/sharer|twitter\.com/intent|wa\.me|whatsapp|t\.me/share|linkedin\.com/share", href):
            continue  # botones de compartir
        etiqueta = a.get_text(" ", strip=True)[:80]
        out.append((etiqueta, href))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogo", default="catalogo_organismos.csv")
    ap.add_argument("--avisos-por-entidad", type=int, default=3)
    ap.add_argument("--solo-cas", action="store_true")
    ap.add_argument("--solo-pendientes", action="store_true", help="solo organismos con analizado=no")
    ap.add_argument("--pausa", type=float, default=3.0)
    a = ap.parse_args()

    cli = Cliente(a.pausa)
    entidades = list(csv.DictReader(open(a.catalogo, encoding="utf-8-sig")))
    if a.solo_pendientes:
        entidades = [e for e in entidades if e.get("analizado") != "sí"]

    f = open("fuentes_detectadas.csv", "w", newline="", encoding="utf-8-sig")
    w = csv.writer(f)
    w.writerow(["entidad", "ficha", "etiqueta", "rol", "url", "dominio", "tipo"])
    resumen = defaultdict(list)
    for i, e in enumerate(entidades, 1):
        print(f"[{i}/{len(entidades)}] {e['entidad']}", file=sys.stderr)
        try:
            fichas = fichas_de_entidad(cli, e["url_ficha_sitio"], a.avisos_por_entidad, a.solo_cas)
        except Exception as ex:
            print("   ! entidad:", ex, file=sys.stderr)
            continue
        for ficha in fichas:
            try:
                for etiqueta, url in enlaces_oficiales(cli, ficha):
                    fila = [e["entidad"], ficha, etiqueta, rol(etiqueta), url,
                            urlparse(url).netloc, clasificar(url, etiqueta)]
                    w.writerow(fila)
                    resumen[e["entidad"]].append(fila)
            except Exception as ex:
                print("   ! ficha:", ex, file=sys.stderr)
        f.flush()
    f.close()

    with open("resumen_por_entidad.csv", "w", newline="", encoding="utf-8-sig") as g:
        w = csv.writer(g)
        w.writerow(["entidad", "dominio_principal", "tipo_bases", "ejemplo_bases", "portal_postulacion", "pagina_resultados"])
        for ent, filas in resumen.items():
            dominios = Counter(r[5] for r in filas if r[6] not in ("Video", "Facebook"))
            bases = [r for r in filas if r[3] in ("bases", "perfil")] or filas
            post = next((r[4] for r in filas if r[3] == "postulación"), "")
            res = next((r[4] for r in filas if r[3] in ("resultados", "comunicados")), "")
            w.writerow([ent, dominios.most_common(1)[0][0] if dominios else "", bases[0][6], bases[0][4], post, res])
    print("Listo: fuentes_detectadas.csv y resumen_por_entidad.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
