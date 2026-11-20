# convocatorias-cas

Recolector diario de las convocatorias CAS vigentes publicadas en
[convocatoriasdetrabajo.com](https://www.convocatoriasdetrabajo.com/convocatorias-cas-vigentes.php),
con análisis de las bases en PDF.

## Qué produce y dónde queda

| Salida | Dónde | Para qué |
|---|---|---|
| `data/AAAA-MM-DD.csv` | En el repositorio (commit diario) | Todas las convocatorias vistas ese día. Columna `nueva_hoy` marca las nuevas. Se abre en Excel. |
| `informes/AAAA-MM-DD.md` | En el repositorio (commit diario) | Lista de convocatorias nuevas y resumen de cada PDF de bases. GitHub lo muestra formateado. |
| `cas.db` (SQLite histórico) | [Release **datos**](../../releases/tag/datos) | Base completa con el texto de cada ficha y la tabla `bases_pdf`. Se reemplaza cada día. |

Los PDF de bases **no se guardan**: se descargan, se analizan en memoria y solo se
conservan el enlace y lo extraído. La base SQLite va como archivo del Release y no
en git, para que el repositorio no crezca con cada versión.

## Ejecutarlo a mano en tu computadora

```bash
git clone https://github.com/Data-driven-club/convocatorias-cas.git
cd convocatorias-cas
python -m venv .venv && source .venv/bin/activate   # en Windows: .venv\Scripts\activate
pip install -r requirements.txt

python diario.py                                   # listado CAS completo
python diario.py --departamento 15 --max-paginas 5 # solo Lima, 5 páginas (prueba rápida)
```

Para continuar desde la base histórica publicada, descarga antes `cas.db` del
Release **datos** y déjala en la carpeta del repositorio.

Opciones de `diario.py`: `--departamento`, `--carrera`, `--nivel` (códigos del sitio),
`--max-paginas` (200; se detiene solo al final del listado), `--pausa` entre peticiones en segundos (3), `--max-pdfs` por día (40),
`--sin-scraping` (rehace CSV e informe con la base actual).

Consultas de ejemplo sobre la base:

```bash
sqlite3 cas.db "SELECT entidad, titulo, sueldo, exp_general_anios FROM convocatorias
                WHERE sueldo >= 6000 AND exp_general_anios <= 4 ORDER BY sueldo DESC;"
sqlite3 cas.db "SELECT titulo, entidad FROM busqueda WHERE busqueda MATCH 'SIAF';"
```

## Ejecución automática (GitHub Actions)

El workflow [`.github/workflows/diario.yml`](.github/workflows/diario.yml) corre todos
los días a las **7:17 a. m. hora de Lima** (12:17 UTC). GitHub puede retrasar unos
minutos las tareas programadas.

- **Correrlo ahora:** pestaña **Actions** → *Scraping diario CAS* → **Run workflow**.
  Ahí puedes poner filtros (departamento, carrera, nivel, páginas) solo para esa corrida.
- **Filtros fijos para la corrida diaria (opcional):** Settings → Secrets and variables →
  Actions → pestaña *Variables*, y crea `DEPARTAMENTO`, `CARRERA`, `NIVEL` o `MAX_PAGINAS`.
- **Permisos:** el workflow usa el token automático de GitHub. Si el commit falla, en
  Settings → Actions → General → *Workflow permissions* elige **Read and write permissions**.
- GitHub desactiva las tareas programadas de un repositorio público tras 60 días sin
  actividad; los commits diarios del propio workflow cuentan como actividad.

La primera corrida descarga el detalle de todas las convocatorias y puede tardar más
de una hora; las siguientes solo bajan las nuevas.

## Estructura

```
diario.py                     corrida diaria: scraper + CSV + análisis de PDF + informe
scraper_cas.py                scraper del listado y las fichas -> SQLite (incremental)
organismos/
  catalogo_organismos.csv     catálogo de organismos y sus fuentes oficiales
  descubrir_fuentes.py        paso 1: enlaces oficiales por organismo
  analizar_bases_pdf.py       paso 2: diagnóstico de las bases en PDF
```

## Scraping responsable

Una petición cada 3 segundos, User-Agent identificable, respeta `robots.txt` y no
vuelve a descargar fichas que ya están en la base. Uso personal.
