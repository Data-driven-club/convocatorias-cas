# Dashboard de convocatorias CAS en Looker Studio

Looker Studio no lee un CSV de GitHub directamente, así que el camino es:

```
GitHub Actions (7:17 a. m.)  ->  data/looker/*.csv  ->  Google Sheets (IMPORTDATA)  ->  Looker Studio
```

El workflow diario reescribe los dos CSV con la misma URL. La hoja de Google los
vuelve a importar sola (aprox. cada hora) y Looker Studio lee la hoja. Una vez armado,
no hay que tocar nada más.

## Las fuentes

| Archivo | Una fila por | URL para IMPORTDATA |
|---|---|---|
| `convocatorias.csv` | convocatoria vista alguna vez | `https://raw.githubusercontent.com/Data-driven-club/convocatorias-cas/main/data/looker/convocatorias.csv` |
| `resumen_diario.csv` | día de corrida | `https://raw.githubusercontent.com/Data-driven-club/convocatorias-cas/main/data/looker/resumen_diario.csv` |

### Campos de `convocatorias.csv`

| Campo | Tipo en Looker | Qué es |
|---|---|---|
| `id` | Texto | Id del aviso en el sitio |
| `entidad`, `titulo` | Texto | Organismo y puesto |
| `tipo_entidad` | Texto | Salud, Educación (UGEL/DRE), Universidad, Ministerio, Municipalidad, Justicia, Gobierno regional, Programa social o nacional, Organismo especializado, Otro (deducido del nombre) |
| `departamento` | Texto | Normalizado (Áncash, Apurímac, San Martín… sin duplicados por tildes o mayúsculas) |
| `region_iso` | Geo › Subdivisión de país (1.er nivel) | Código ISO, p. ej. `PE-LIM`, para el mapa |
| `sueldo` | Número (moneda PEN) | Remuneración mensual en soles |
| `rango_sueldo` | Texto | Tramos ordenables: `1) Hasta 1 500` … `6) Más de 9 000` |
| `vacantes` | Número | |
| `vigente_hasta` | Fecha | Fecha límite de postulación |
| `nivel_formacion` | Texto | Posgrado / especialidad, Título universitario, Bachiller, Egresado / estudiante, Técnico, Secundaria, Otro (deducido del requisito) |
| `requisito_card`, `formacion` | Texto | Requisito de formación tal como lo publica el aviso (útil para buscar por carrera) |
| `cursos`, `exp_especifica_texto` | Texto | Cursos pedidos y detalle de la experiencia específica |
| `exp_general_anios`, `exp_especifica_anios`, `exp_publico_anios` | Número | Años de experiencia pedidos (vacío = sin dato) |
| `rango_experiencia` | Texto | Tramos de experiencia general (si la ficha solo pide experiencia específica, se usa esa) |
| `fecha_publicacion` | Fecha | Día en que la vimos por primera vez |
| `ultima_vez_vista` | Fecha | Última corrida en que seguía publicada |
| `vigente` | Texto `sí`/`no` | Sigue publicada en la última corrida y su plazo no venció |
| `nueva` | Texto `sí`/`no` | Apareció por primera vez en la última corrida |
| `tiene_bases` | Texto `sí`/`no` | Hay enlace a las bases |
| `url`, `enlace_bases` | URL | Aviso y bases |

### Campos de `resumen_diario.csv`

`fecha`, `convocatorias_vistas`, `nuevas`, `vacantes`, `sueldo_mediano`.
El primer día (2026-11-19) cuenta todo como nuevo porque fue la carga inicial.

## Paso 1. Crear la hoja de Google (5 minutos)

1. Entra a <https://sheets.new> con tu cuenta de Google y ponle de nombre **Convocatorias CAS**.
2. Renombra la primera pestaña a `convocatorias` y en la celda **A1** pega:
   ```
   =IMPORTDATA("https://raw.githubusercontent.com/Data-driven-club/convocatorias-cas/main/data/looker/convocatorias.csv"; ","; "en_US")
   ```
3. Crea una segunda pestaña `resumen_diario` y en **A1** pega:
   ```
   =IMPORTDATA("https://raw.githubusercontent.com/Data-driven-club/convocatorias-cas/main/data/looker/resumen_diario.csv"; ","; "en_US")
   ```
4. Revisa que los sueldos se vean como `3064` y no como `30640` o texto. Si algo sale raro:
   **Archivo › Configuración › Configuración regional: Estados Unidos**, y en las fórmulas
   cambia `;` por `,`.

> Si tu hoja está en inglés/EE. UU., el separador de la fórmula es `,` en vez de `;`.
> El `"en_US"` hace que se lea el punto decimal correctamente aunque tu hoja esté en español.

## Paso 2. Conectar Looker Studio

1. Entra a <https://lookerstudio.google.com> › **Crear › Fuente de datos › Hojas de cálculo de Google**.
2. Elige **Convocatorias CAS** › pestaña `convocatorias`, deja marcado *Usar la primera fila como encabezado* › **Conectar**.
3. En la lista de campos ajusta los tipos (columna *Tipo*):
   - `vigente_hasta`, `fecha_publicacion`, `ultima_vez_vista` › **Fecha y hora › Fecha (AAAAMMDD)**
   - `region_iso` › **Geográfico › Subdivisión de país (1.er nivel)**
   - `sueldo` › **Moneda › PEN (S/)** y agregación **Promedio**
   - `id` › **Texto**, agregación **Ninguna**
   - `url`, `enlace_bases` › **URL**
4. Agrega estos **campos calculados** (botón *Agregar un campo*):

   | Nombre | Fórmula |
   |---|---|
   | `Convocatorias` | `COUNT_DISTINCT(id)` |
   | `Sueldo mediano` | `MEDIAN(sueldo)` |
   | `Días para el cierre` | `DATE_DIFF(vigente_hasta, TODAY())` |
   | `Puesto (enlace)` | `HYPERLINK(url, titulo)` |

5. En la parte superior, **Actualización de los datos**: 1 hora es suficiente.
6. Repite 1–2 con la pestaña `resumen_diario` (pon `fecha` como **Fecha**). Así tendrás dos fuentes.

## Paso 3. Armar el dashboard

Crea un informe (**Crear › Informe**) y agrega la fuente `convocatorias`.

### Página 1 · Convocatorias vigentes

- **Filtro de página** (Página › Configuración actual de la página › Filtros): `vigente` igual a `sí`.
- **Controles** en la parte superior (Agregar un control › Lista desplegable): `departamento`,
  `tipo_entidad`, `nivel_formacion`, `rango_sueldo`, `rango_experiencia`. Un **control deslizante**
  sobre `sueldo` también ayuda, y un **cuadro de búsqueda** (Control › Cuadro de entrada) sobre
  `formacion` o `cursos` para buscar por carrera o curso (p. ej. "Contabilidad", "SIAF").
- **Fila de indicadores** (Cuadro de resultados):
  - `Convocatorias`
  - `vacantes` (Suma)
  - `Sueldo mediano`
  - `Convocatorias` con filtro `nueva = sí` y título "Nuevas hoy"
- **Mapa coroplético** (Gráfico › Mapa geográfico): dimensión `region_iso`, métrica `Convocatorias`,
  zona *Perú*.
- **Barras horizontales**: `tipo_entidad` × `Convocatorias`; `departamento` × `Sueldo mediano`.
- **Columnas**: `rango_sueldo` × `Convocatorias` (ordena por `rango_sueldo` ascendente; el número
  inicial de cada tramo es para que queden en orden) y lo mismo con `rango_experiencia`.
- **Tabla** con `Puesto (enlace)`, `entidad`, `departamento`, `sueldo`, `vacantes`, `vigente_hasta`,
  `Días para el cierre`, `exp_general_anios`, `nivel_formacion`; orden por `vigente_hasta` ascendente
  para ver primero las que cierran pronto. Activa *Formato condicional* en `Días para el cierre ≤ 2`.

### Página 2 · Tendencia

Con la fuente `resumen_diario`:

- **Serie temporal**: dimensión `fecha`, métricas `nuevas` y `convocatorias_vistas`.
- **Serie temporal**: `fecha` × `sueldo_mediano`.

Con la fuente `convocatorias` (sin el filtro `vigente`):

- **Barras apiladas**: `fecha_publicacion` × `Convocatorias`, desglose `tipo_entidad`.

### Compartir

Botón **Compartir** del informe: *Cualquier persona con el enlace puede ver*. Quien lo vea no
necesita acceso a la hoja de cálculo (las credenciales de la fuente son las tuyas, del propietario).

## Si algo falla

- **La hoja muestra `#N/A` o "No se pudo obtener la URL"**: abre la URL raw en el navegador; si carga,
  borra y vuelve a pegar la fórmula (Sheets reintenta). GitHub a veces tarda unos minutos tras el commit diario.
- **Los datos no se actualizan**: IMPORTDATA se recalcula aprox. cada hora; Looker Studio, según la
  *Actualización de los datos* de la fuente. En el informe, menú *⋮ › Actualizar datos* fuerza la lectura.
- **Cambié columnas en el CSV**: en Looker, *Recursos › Administrar fuentes de datos › Editar ›
  Actualizar campos*.
- **El CSV crece mucho**: hoy pesa unos 1,2 MB con 1 568 filas. Si con los meses IMPORTDATA empieza
  a fallar por tamaño, se puede limitar `looker.py` a los últimos N días.
