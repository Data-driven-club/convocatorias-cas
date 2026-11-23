/* Dashboard de convocatorias CAS: sin dependencias, lee los CSV de data/looker/. */
(function () {
  "use strict";

  // En GitHub Pages los CSV se copian a data/; en local se leen desde el repositorio.
  var RUTAS = {
    convocatorias: ["data/convocatorias.csv", "../data/looker/convocatorias.csv"],
    resumen: ["data/resumen_diario.csv", "../data/looker/resumen_diario.csv"]
  };
  var POR_PAGINA = 25;
  var CAMPOS_FILTRO = ["departamento", "tipo_entidad", "nivel_formacion", "rango_sueldo", "rango_experiencia"];
  var NOMBRES = {
    departamento: "Región", tipo_entidad: "Tipo de entidad", nivel_formacion: "Formación",
    rango_sueldo: "Sueldo", rango_experiencia: "Experiencia", entidad: "Entidad", texto: "Búsqueda"
  };
  var ORDEN_FORMACION = ["Secundaria", "Técnico", "Egresado / estudiante", "Bachiller", "Título universitario", "Posgrado / especialidad", "Otro", "Sin dato"];

  var nf = new Intl.NumberFormat("es-PE");
  var MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "set", "oct", "nov", "dic"];
  var MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre"];
  var DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

  var todas = [];       // todas las convocatorias
  var resumen = [];     // una fila por día de corrida
  var ultima = "";      // fecha de la última corrida
  var estado = nuevoEstado();
  var pagina = 0;
  var abiertas = {};    // filas de la tabla desplegadas
  var $ = function (id) { return document.getElementById(id); };

  function nuevoEstado() {
    return { texto: "", entidad: "", vigente: true, nueva: false, orden: "fecha_publicacion:desc",
             departamento: "", tipo_entidad: "", nivel_formacion: "", rango_sueldo: "", rango_experiencia: "" };
  }

  /* ---------- utilidades ---------- */

  function parseCSV(txt) {
    var filas = [], fila = [], campo = "", i = 0, comillas = false, n = txt.length;
    if (txt.charCodeAt(0) === 0xFEFF) i = 1;
    for (; i < n; i++) {
      var c = txt[i];
      if (comillas) {
        if (c === '"') { if (txt[i + 1] === '"') { campo += '"'; i++; } else comillas = false; }
        else campo += c;
      } else if (c === '"') comillas = true;
      else if (c === ",") { fila.push(campo); campo = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && txt[i + 1] === "\n") i++;
        fila.push(campo); filas.push(fila); fila = []; campo = "";
      } else campo += c;
    }
    if (campo !== "" || fila.length) { fila.push(campo); filas.push(fila); }
    var cab = filas.shift() || [];
    return filas.filter(function (f) { return f.length > 1; }).map(function (f) {
      var o = {};
      cab.forEach(function (k, j) { o[k] = f[j] === undefined ? "" : f[j]; });
      return o;
    });
  }

  function cargar(rutas) {
    var i = 0;
    function intento() {
      return fetch(rutas[i], { cache: "no-cache" }).then(function (r) {
        if (!r.ok) throw new Error(r.status + " " + rutas[i]);
        return r.text();
      }).catch(function (e) {
        i++;
        if (i < rutas.length) return intento();
        throw e;
      });
    }
    return intento();
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function urlSegura(u) { return /^https?:\/\//i.test(u || "") ? u : ""; }
  function normal(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }
  function sinPrefijo(etq) { return (etq || "").replace(/^\d\)\s*/, ""); }
  function fechaCorta(f) {
    if (!f) return "";
    var p = f.split("-");
    return parseInt(p[2], 10) + " " + MESES[parseInt(p[1], 10) - 1];
  }
  function fechaLarga(f) {
    var p = f.split("-"), d = new Date(+p[0], +p[1] - 1, +p[2]);
    return DIAS[d.getDay()] + " " + (+p[2]) + " de " + MESES_LARGOS[+p[1] - 1] + " de " + p[0];
  }
  function diasEntre(a, b) {
    var pa = a.split("-"), pb = b.split("-");
    return Math.round((Date.UTC(+pb[0], +pb[1] - 1, +pb[2]) - Date.UTC(+pa[0], +pa[1] - 1, +pa[2])) / 864e5);
  }
  function mediana(v) {
    if (!v.length) return null;
    var s = v.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  function cuantil(v, q) {
    if (!v.length) return null;
    var s = v.slice().sort(function (a, b) { return a - b; });
    return s[Math.min(s.length - 1, Math.floor(q * s.length))];
  }
  function soles(x) { return x == null ? "–" : "S/ " + nf.format(Math.round(x)); }

  // Quita la publicidad del sitio que se cuela en el campo de cursos ("📢 Diploma GRATUITO…")
  function limpiarCursos(t) {
    return (t || "").split(" | ").filter(function (p) {
      return p && !/📢|GRATUITO|⛏|🎓|👉/.test(p);
    }).join(" | ");
  }

  /* ---------- datos ---------- */

  function preparar(filas) {
    filas.forEach(function (r) {
      r._sueldo = r.sueldo ? parseFloat(r.sueldo) : null;
      r._vacantes = r.vacantes ? parseFloat(r.vacantes) : 0;
      r._exp = r.exp_general_anios ? parseFloat(r.exp_general_anios) : null;
      r.cursos = limpiarCursos(r.cursos);
      r._busca = normal([r.titulo, r.entidad, r.formacion, r.requisito_card, r.cursos, r.exp_especifica_texto].join(" "));
      r._entidad = normal(r.entidad);
      if (r.ultima_vez_vista > ultima) ultima = r.ultima_vez_vista;
    });
    return filas;
  }

  // Filtra con el estado actual; `salvo` ignora ese campo (para el gráfico del propio campo).
  function filtrar(salvo) {
    var omitir = [].concat(salvo || []);
    var tokens = normal(estado.texto).split(/\s+/).filter(Boolean);
    var ent = normal(estado.entidad).trim();
    return todas.filter(function (r) {
      if (estado.vigente && omitir.indexOf("vigente") < 0 && r.vigente !== "sí") return false;
      if (estado.nueva && omitir.indexOf("nueva") < 0 && r.nueva !== "sí") return false;
      for (var i = 0; i < CAMPOS_FILTRO.length; i++) {
        var c = CAMPOS_FILTRO[i];
        if (omitir.indexOf(c) < 0 && estado[c] && r[c] !== estado[c]) return false;
      }
      if (ent && omitir.indexOf("entidad") < 0 && r._entidad.indexOf(ent) < 0) return false;
      for (var j = 0; j < tokens.length; j++) if (r._busca.indexOf(tokens[j]) < 0) return false;
      return true;
    });
  }

  function contar(filas, campo) {
    var m = {};
    filas.forEach(function (r) { var k = r[campo] || "Sin dato"; m[k] = (m[k] || 0) + 1; });
    return m;
  }

  function opciones(campo) {
    var vistos = {};
    todas.forEach(function (r) { vistos[r[campo] || "Sin dato"] = 1; });
    var l = Object.keys(vistos);
    if (campo === "nivel_formacion") l.sort(function (a, b) { return ORDEN_FORMACION.indexOf(a) - ORDEN_FORMACION.indexOf(b); });
    else l.sort(function (a, b) { return a.localeCompare(b, "es"); });
    return l;
  }

  /* ---------- estado en la URL (para compartir un filtro) ---------- */

  function leerHash() {
    var p = new URLSearchParams(location.hash.slice(1));
    var e = nuevoEstado();
    Object.keys(e).forEach(function (k) {
      if (!p.has(k)) return;
      e[k] = typeof e[k] === "boolean" ? p.get(k) === "1" : p.get(k);
    });
    estado = e;
  }
  function escribirHash() {
    var base = nuevoEstado(), p = new URLSearchParams();
    Object.keys(estado).forEach(function (k) {
      if (estado[k] === base[k]) return;
      p.set(k, typeof estado[k] === "boolean" ? (estado[k] ? "1" : "0") : estado[k]);
    });
    var h = p.toString();
    history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
  }

  /* ---------- controles ---------- */

  function montarControles() {
    CAMPOS_FILTRO.forEach(function (c) {
      var sel = $("f-" + c);
      sel.innerHTML = '<option value="">Todas</option>' + opciones(c).map(function (o) {
        return '<option value="' + esc(o) + '">' + esc(c === "rango_sueldo" || c === "rango_experiencia" ? sinPrefijo(o) : o) + "</option>";
      }).join("");
      sel.addEventListener("change", function () { estado[c] = sel.value; cambio(); });
    });
    var ents = {};
    todas.forEach(function (r) { ents[r.entidad] = 1; });
    $("lista-entidades").innerHTML = Object.keys(ents).sort(function (a, b) { return a.localeCompare(b, "es"); })
      .map(function (e) { return '<option value="' + esc(e) + '">'; }).join("");

    var t;
    $("f-texto").addEventListener("input", function (ev) {
      clearTimeout(t);
      t = setTimeout(function () { estado.texto = ev.target.value; cambio(); }, 180);
    });
    $("f-entidad").addEventListener("input", function (ev) {
      clearTimeout(t);
      t = setTimeout(function () { estado.entidad = ev.target.value; cambio(); }, 180);
    });
    $("f-vigente").addEventListener("change", function (ev) { estado.vigente = ev.target.checked; cambio(); });
    $("f-nueva").addEventListener("change", function (ev) { estado.nueva = ev.target.checked; cambio(); });
    $("orden").addEventListener("change", function (ev) { estado.orden = ev.target.value; pagina = 0; escribirHash(); pintarTabla(filtrar()); });
    $("limpiar").addEventListener("click", function () { estado = nuevoEstado(); cambio(); });
    $("t-anterior").addEventListener("click", function () { pagina--; pintarTabla(filtrar()); arribaTabla(); });
    $("t-siguiente").addEventListener("click", function () { pagina++; pintarTabla(filtrar()); arribaTabla(); });
    $("descargar").addEventListener("click", descargar);
    $("tema").addEventListener("click", alternarTema);
    $("t-cuerpo").addEventListener("click", function (ev) {
      if (ev.target.closest("a")) return;
      var tr = ev.target.closest("tr.fila");
      if (!tr) return;
      abiertas[tr.dataset.id] = !abiertas[tr.dataset.id];
      pintarTabla(filtrar());
    });
    $("t-cuerpo").addEventListener("keydown", function (ev) {
      if ((ev.key === "Enter" || ev.key === " ") && ev.target.matches("tr.fila")) {
        ev.preventDefault();
        abiertas[ev.target.dataset.id] = !abiertas[ev.target.dataset.id];
        pintarTabla(filtrar());
        var f = document.querySelector('tr.fila[data-id="' + ev.target.dataset.id + '"]');
        if (f) f.focus();
      }
    });
  }

  function arribaTabla() {
    var y = document.querySelector(".tabla-tarjeta").getBoundingClientRect().top + scrollY - 8;
    if (y < scrollY) scrollTo(0, y);
  }

  function sincronizarControles() {
    CAMPOS_FILTRO.forEach(function (c) {
      var s = $("f-" + c);
      s.value = estado[c];
      s.classList.toggle("activo", !!estado[c]);
    });
    if ($("f-texto").value !== estado.texto) $("f-texto").value = estado.texto;
    if ($("f-entidad").value !== estado.entidad) $("f-entidad").value = estado.entidad;
    $("f-entidad").classList.toggle("activo", !!estado.entidad);
    $("f-vigente").checked = estado.vigente;
    $("f-nueva").checked = estado.nueva;
    $("orden").value = estado.orden;
  }

  function alternarTema() {
    var raiz = document.documentElement;
    var oscuro = raiz.dataset.theme ? raiz.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    raiz.dataset.theme = oscuro ? "light" : "dark";
    try { localStorage.setItem("tema", raiz.dataset.theme); } catch (e) { /* sin almacenamiento */ }
  }

  function cambio() {
    pagina = 0;
    escribirHash();
    pintar();
  }

  /* ---------- tooltip ---------- */

  var tip = $("tooltip");
  function mostrarTip(html, ev) {
    tip.innerHTML = html;
    tip.hidden = false;
    var x = ev.clientX + 14, y = ev.clientY + 14, r = tip.getBoundingClientRect();
    if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
    if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
    tip.style.left = Math.max(8, x) + "px";
    tip.style.top = Math.max(8, y) + "px";
  }
  function ocultarTip() { tip.hidden = true; }

  /* ---------- gráficos SVG ---------- */

  var NS = "http://www.w3.org/2000/svg";
  function el(nombre, attrs, padre) {
    var e = document.createElementNS(NS, nombre);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (padre) padre.appendChild(e);
    return e;
  }
  // Rectángulo con el extremo de datos redondeado (4px) y la base recta.
  function barraH(x, y, w, h) {
    var r = Math.min(4, w / 2, h / 2);
    return "M" + x + "," + y + "h" + (w - r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r +
      "v" + (h - 2 * r) + "a" + r + "," + r + " 0 0 1 " + (-r) + "," + r + "h" + (r - w) + "z";
  }
  function barraV(x, yBase, w, h) {
    var r = Math.min(4, w / 2, h);
    return "M" + x + "," + yBase + "v" + (r - h) + "a" + r + "," + r + " 0 0 1 " + r + "," + (-r) +
      "h" + (w - 2 * r) + "a" + r + "," + r + " 0 0 1 " + r + "," + r + "v" + (h - r) + "z";
  }
  function medir(svg, texto, clase) {
    var t = el("text", { "class": clase, x: -9999, y: -9999 }, svg);
    t.textContent = texto;
    var w = t.getComputedTextLength();
    svg.removeChild(t);
    return w;
  }
  function recortar(svg, texto, clase, max) {
    if (medir(svg, texto, clase) <= max) return texto;
    var s = texto;
    while (s.length > 4 && medir(svg, s + "…", clase) > max) s = s.slice(0, -1);
    return s + "…";
  }

  // Barras horizontales de una sola serie; clic = filtrar por ese valor.
  function graficoBarras(id, campo, items, total, filtroCampo) {
    var cont = $(id);
    cont.innerHTML = "";
    if (!items.length) { cont.innerHTML = '<div class="vacio">Sin convocatorias con estos filtros.</div>'; return; }
    var ancho = Math.max(280, cont.clientWidth), fila = 26, grosor = 16;
    var svg = el("svg", { viewBox: "0 0 " + ancho + " " + (items.length * fila + 4), role: "list", "aria-label": NOMBRES[campo] || campo }, cont);
    var etqAncho = Math.min(ancho * 0.42, Math.max.apply(null, items.map(function (it) { return medir(svg, it.etiqueta, "etq"); })) + 14);
    etqAncho = Math.max(70, Math.min(etqAncho, 230));
    var valAncho = medir(svg, nf.format(items[0].n) + "  (100 %)", "val") + 10;
    var x0 = etqAncho, plot = ancho - x0 - valAncho;
    var max = Math.max.apply(null, items.map(function (it) { return it.n; })) || 1;
    var elegido = estado[filtroCampo || campo];

    el("line", { "class": "base", x1: x0, x2: x0, y1: 0, y2: items.length * fila }, svg);
    items.forEach(function (it, i) {
      var y = i * fila, g = el("g", { "class": "barra-fila", tabindex: 0, role: "listitem" }, svg);
      var pct = total ? Math.round(100 * it.n / total) : 0, pctTxt = pct === 0 && it.n ? "<1" : String(pct);
      if (elegido) g.classList.add(elegido === it.clave ? "elegida" : "apagada");
      g.setAttribute("aria-label", it.etiqueta + ": " + nf.format(it.n) + " convocatorias");
      el("rect", { "class": "fondo", x: 0, y: y + 1, width: ancho, height: fila - 2, rx: 6 }, g);
      var t = el("text", { "class": "etq", x: x0 - 8, y: y + fila / 2 + 4, "text-anchor": "end" }, g);
      t.textContent = recortar(svg, it.etiqueta, "etq", x0 - 10);
      var w = Math.max(2, plot * it.n / max);
      el("path", { "class": "barra", d: barraH(x0, y + (fila - grosor) / 2, w, grosor) }, g);
      var v = el("text", { "class": "val", x: x0 + w + 6, y: y + fila / 2 + 4 }, g);
      v.textContent = nf.format(it.n) + (total ? "  (" + pctTxt + " %)" : "");
      var tipHtml = "<div>" + esc(it.etiqueta) + "</div><b>" + nf.format(it.n) + "</b> convocatorias" +
        (total ? " · " + pctTxt + " %" : "") + (it.vacantes ? "<br>" + nf.format(it.vacantes) + " vacantes" : "") +
        (it.sueldo != null ? "<br>Sueldo mediano " + soles(it.sueldo) : "");
      g.addEventListener("mousemove", function (ev) { mostrarTip(tipHtml, ev); });
      g.addEventListener("mouseleave", ocultarTip);
      var activar = function () {
        ocultarTip();
        var k = filtroCampo || campo;
        estado[k] = estado[k] === it.clave ? "" : it.clave;
        cambio();
      };
      g.addEventListener("click", activar);
      g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); activar(); } });
    });
  }

  function itemsDe(filas, campo, orden) {
    var grupos = {};
    filas.forEach(function (r) {
      var k = r[campo] || "Sin dato";
      (grupos[k] = grupos[k] || []).push(r);
    });
    var items = Object.keys(grupos).map(function (k) {
      var g = grupos[k];
      return {
        clave: k, etiqueta: sinPrefijo(k), n: g.length,
        vacantes: g.reduce(function (s, r) { return s + r._vacantes; }, 0),
        sueldo: mediana(g.map(function (r) { return r._sueldo; }).filter(function (x) { return x != null; }))
      };
    });
    if (orden === "clave") items.sort(function (a, b) {
      if (a.clave === "Sin dato") return 1;
      if (b.clave === "Sin dato") return -1;
      return a.clave.localeCompare(b.clave, "es");
    });
    else if (orden === "formacion") items.sort(function (a, b) { return ORDEN_FORMACION.indexOf(a.clave) - ORDEN_FORMACION.indexOf(b.clave); });
    else items.sort(function (a, b) { return b.n - a.n || a.clave.localeCompare(b.clave, "es"); });
    return items;
  }

  function ticks(max) {
    if (max <= 0) return [0];
    var paso = Math.pow(10, Math.floor(Math.log10(max)));
    [1, 2, 2.5, 5, 10].some(function (m) { if (max / (paso * m) <= 4) { paso *= m; return true; } return false; });
    var t = [];
    for (var v = 0; v <= max + 1e-9; v += paso) t.push(v);
    if (t[t.length - 1] < max) t.push(t[t.length - 1] + paso);
    return t;
  }

  // Columnas: convocatorias nuevas por día de publicación (respeta los filtros).
  function graficoTendencia(filas) {
    var cont = $("g-tendencia");
    cont.innerHTML = "";
    var fechas = resumen.map(function (d) { return d.fecha; });
    var inicial = fechas[0];
    // El primer día de la serie es la carga inicial (todo lo que ya estaba publicado): se omite.
    var dias = fechas.slice(1);
    var porDia = {};
    filas.forEach(function (r) { porDia[r.fecha_publicacion] = (porDia[r.fecha_publicacion] || 0) + 1; });
    $("g-tendencia-sub").textContent = dias.length
      ? "Según el día en que aparecieron por primera vez (incluye las ya vencidas). El " + fechaCorta(inicial) + " fue la carga inicial (" +
        nf.format(porDia[inicial] || 0) + " con estos filtros) y no se muestra."
      : "Hace falta más de un día de corridas para ver la tendencia.";
    if (!dias.length) return;

    var ancho = Math.max(280, cont.clientWidth), alto = 190, izq = 36, der = 8, arriba = 18, abajo = 24;
    var svg = el("svg", { viewBox: "0 0 " + ancho + " " + alto, role: "img",
      "aria-label": "Nuevas por día: " + dias.map(function (d) { return fechaCorta(d) + " " + (porDia[d] || 0); }).join(", ") }, cont);
    var vals = dias.map(function (d) { return porDia[d] || 0; });
    var tk = ticks(Math.max.apply(null, vals)), top = tk[tk.length - 1] || 1;
    var plotH = alto - arriba - abajo, plotW = ancho - izq - der;
    var y = function (v) { return arriba + plotH - plotH * v / top; };
    tk.forEach(function (v) {
      el("line", { "class": v === 0 ? "base" : "rejilla", x1: izq, x2: ancho - der, y1: y(v), y2: y(v) }, svg);
      var t = el("text", { "class": "eje-texto", x: izq - 6, y: y(v) + 4, "text-anchor": "end" }, svg);
      t.textContent = nf.format(v);
    });
    var banda = plotW / dias.length, w = Math.min(24, banda * 0.6);
    var cadaCuanto = Math.ceil(dias.length / Math.max(1, Math.floor(plotW / 48)));
    dias.forEach(function (d, i) {
      var cx = izq + banda * i + banda / 2, v = vals[i];
      var g = el("g", { "class": "columna-grupo" }, svg);
      el("rect", { "class": "golpe", x: cx - banda / 2, y: arriba, width: banda, height: plotH, rx: 4 }, g);
      if (v > 0) el("path", { "class": "columna-barra", d: barraV(cx - w / 2, y(0), w, y(0) - y(v)) }, g);
      if (i % cadaCuanto === 0 || i === dias.length - 1) {
        var t = el("text", { "class": "eje-texto", x: cx, y: alto - 6, "text-anchor": "middle" }, svg);
        t.textContent = fechaCorta(d);
      }
      if (i === dias.length - 1) {
        var vt = el("text", { "class": "val", x: cx, y: y(v) - 6, "text-anchor": "middle" }, svg);
        vt.textContent = nf.format(v);
      }
      var html = "<div>" + esc(fechaLarga(d)) + "</div><b>" + nf.format(v) + "</b> nuevas";
      g.addEventListener("mousemove", function (ev) { mostrarTip(html, ev); });
      g.addEventListener("mouseleave", ocultarTip);
    });
  }

  /* ---------- KPIs ---------- */

  function pintarKpis(filas) {
    var sueldos = filas.map(function (r) { return r._sueldo; }).filter(function (x) { return x != null; });
    var vigentes = todas.filter(function (r) { return r.vigente === "sí"; }).length;
    $("k-total").textContent = nf.format(filas.length);
    $("k-total-nota").textContent = estado.vigente ? "de " + nf.format(vigentes) + " vigentes en total" : "incluye vencidas o retiradas";
    $("k-nuevas").textContent = nf.format(filas.filter(function (r) { return r.nueva === "sí"; }).length);
    $("k-nuevas-nota").textContent = "aparecieron el " + fechaCorta(ultima);
    $("k-vacantes").textContent = nf.format(filas.reduce(function (s, r) { return s + r._vacantes; }, 0));
    $("k-sueldo").textContent = soles(mediana(sueldos));
    $("k-sueldo-nota").textContent = sueldos.length
      ? "la mitad gana entre " + soles(cuantil(sueldos, 0.25)) + " y " + soles(cuantil(sueldos, 0.75)) : "";
  }

  /* ---------- tabla ---------- */

  function ordenar(filas) {
    var p = estado.orden.split(":"), c = p[0], dir = p[1] === "asc" ? 1 : -1;
    var val = function (r) {
      if (c === "sueldo") return r._sueldo;
      if (c === "vacantes") return r._vacantes;
      return r[c] || null;
    };
    return filas.slice().sort(function (a, b) {
      var x = val(a), y = val(b);
      if (x == null && y == null) return b.id - a.id;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * dir || b.id - a.id;
    });
  }

  function marcar(texto) {
    var h = esc(texto), tokens = normal(estado.texto).split(/\s+/).filter(function (t) { return t.length > 1; });
    if (!tokens.length) return h;
    // Marca coincidencias ignorando tildes: se busca sobre una versión normalizada del mismo largo.
    var plano = normal(h), marcas = [];
    tokens.forEach(function (t) {
      var t2 = esc(t), i = 0;
      while ((i = plano.indexOf(t2, i)) >= 0) { marcas.push([i, i + t2.length]); i += t2.length; }
    });
    if (!marcas.length || plano.length !== h.length) return h;
    marcas.sort(function (a, b) { return a[0] - b[0]; });
    var out = "", pos = 0;
    marcas.forEach(function (m) {
      if (m[0] < pos) return;
      out += h.slice(pos, m[0]) + '<mark class="marca">' + h.slice(m[0], m[1]) + "</mark>";
      pos = m[1];
    });
    return out + h.slice(pos);
  }

  function pintarTabla(filas) {
    var ord = ordenar(filas), paginas = Math.max(1, Math.ceil(ord.length / POR_PAGINA));
    pagina = Math.min(Math.max(0, pagina), paginas - 1);
    var vista = ord.slice(pagina * POR_PAGINA, (pagina + 1) * POR_PAGINA);
    $("t-sub").textContent = nf.format(filas.length) + " convocatorias. Clic en una fila para ver requisitos y cursos.";
    $("t-pagina").textContent = "Página " + (pagina + 1) + " de " + paginas;
    $("t-anterior").disabled = pagina === 0;
    $("t-siguiente").disabled = pagina >= paginas - 1;
    if (!vista.length) {
      $("t-cuerpo").innerHTML = '<tr><td colspan="8" class="vacio" style="padding:24px;color:var(--tenue)">Ninguna convocatoria coincide con los filtros.</td></tr>';
      return;
    }
    $("t-cuerpo").innerHTML = vista.map(function (r) {
      var abierta = !!abiertas[r.id];
      var dias = r.vigente_hasta && ultima ? diasEntre(ultima, r.vigente_hasta) : null;
      var vence = r.vigente_hasta ? fechaCorta(r.vigente_hasta) : "–";
      var claseVence = dias != null && dias >= 0 && dias <= 3 ? "vence-pronto" : "";
      var ficha = urlSegura(r.url), bases = urlSegura(r.enlace_bases);
      var h = '<tr class="fila" tabindex="0" data-id="' + esc(r.id) + '" aria-expanded="' + abierta + '">' +
        '<td><div class="puesto">' + marcar(r.titulo) +
          (r.nueva === "sí" ? '<span class="chip chip-nueva">Nueva</span>' : "") +
          (r.vigente !== "sí" ? '<span class="chip chip-cerrada">No vigente</span>' : "") + "</div>" +
          '<div class="entidad">' + marcar(r.entidad) + "</div></td>" +
        '<td data-c="Región">' + esc(r.departamento) + "</td>" +
        '<td class="num" data-c="Sueldo">' + (r._sueldo != null ? nf.format(r._sueldo) : "–") + "</td>" +
        '<td class="num" data-c="Vacantes">' + (r.vacantes || "–") + "</td>" +
        '<td data-c="Formación">' + esc(r.nivel_formacion) + "</td>" +
        '<td class="num" data-c="Experiencia">' + (r._exp != null ? nf.format(r._exp) : "–") + "</td>" +
        '<td data-c="Vence" class="' + claseVence + '">' + esc(vence) +
          (dias != null && dias >= 0 && dias <= 3 ? (dias === 0 ? " (hoy)" : " (" + dias + " d)") : "") + "</td>" +
        '<td class="enlaces">' +
          (ficha ? '<a href="' + esc(ficha) + '" target="_blank" rel="noopener">Ficha</a>' : "") +
          (bases ? '<a href="' + esc(bases) + '" target="_blank" rel="noopener">Bases</a>' : "") + "</td></tr>";
      if (abierta) {
        var filasDetalle = [
          ["Requisito de formación", r.formacion || r.requisito_card],
          ["Experiencia específica", r.exp_especifica_texto],
          ["Años de experiencia", [r.exp_general_anios && "general " + r.exp_general_anios,
            r.exp_especifica_anios && "específica " + r.exp_especifica_anios,
            r.exp_publico_anios && "sector público " + r.exp_publico_anios].filter(Boolean).join(" · ")],
          ["Cursos y especialización", r.cursos],
          ["Tipo de entidad", r.tipo_entidad],
          ["Publicada", r.fecha_publicacion ? fechaLarga(r.fecha_publicacion) : ""],
          ["Vigente hasta", r.vigente_hasta ? fechaLarga(r.vigente_hasta) : ""]
        ].filter(function (d) { return d[1]; });
        h += '<tr class="detalle"><td colspan="8"><dl>' + filasDetalle.map(function (d) {
          return "<dt>" + esc(d[0]) + "</dt><dd>" + marcar(d[1]) + "</dd>";
        }).join("") + "</dl></td></tr>";
      }
      return h;
    }).join("");
  }

  function descargar() {
    var cols = ["id", "titulo", "entidad", "tipo_entidad", "departamento", "sueldo", "vacantes", "nivel_formacion",
      "formacion", "exp_general_anios", "exp_especifica_anios", "cursos", "fecha_publicacion", "vigente_hasta", "url", "enlace_bases"];
    var q = function (v) { v = String(v == null ? "" : v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
    var txt = "﻿" + cols.join(",") + "\n" + ordenar(filtrar()).map(function (r) {
      return cols.map(function (c) { return q(r[c]); }).join(",");
    }).join("\n") + "\n";
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([txt], { type: "text/csv;charset=utf-8" }));
    a.download = "convocatorias_cas_" + ultima + ".csv";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  /* ---------- pintar todo ---------- */

  function pintarResumenFiltros(n) {
    var partes = [];
    CAMPOS_FILTRO.forEach(function (c) { if (estado[c]) partes.push(NOMBRES[c] + ": " + sinPrefijo(estado[c])); });
    if (estado.entidad) partes.push("Entidad: " + estado.entidad);
    if (estado.texto.trim()) partes.push("“" + estado.texto.trim() + "”");
    if (estado.nueva) partes.push("solo nuevas");
    if (!estado.vigente) partes.push("incluye no vigentes");
    $("resumen-filtros").textContent = partes.length ? nf.format(n) + " convocatorias con " + partes.join(" · ") : "";
  }

  function pintar() {
    sincronizarControles();
    var filas = filtrar();
    pintarResumenFiltros(filas.length);
    pintarKpis(filas);
    graficoTendencia(filtrar(["nueva", "vigente"]));
    var g = function (campo, orden) {
      var base = filtrar(campo);
      graficoBarras("g-" + campo, campo, itemsDe(base, campo, orden), base.length);
    };
    g("departamento");
    g("rango_sueldo", "clave");
    g("nivel_formacion", "formacion");
    g("tipo_entidad");
    g("rango_experiencia", "clave");
    var baseEnt = filtrar("entidad");
    graficoBarras("g-entidad", "entidad", itemsDe(baseEnt, "entidad").slice(0, 12), baseEnt.length, "entidad");
    pintarTabla(filas);
  }

  /* ---------- inicio ---------- */

  try { var t = localStorage.getItem("tema"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* sin almacenamiento */ }

  Promise.all([cargar(RUTAS.convocatorias), cargar(RUTAS.resumen)]).then(function (txt) {
    todas = preparar(parseCSV(txt[0]));
    resumen = parseCSV(txt[1]).sort(function (a, b) { return a.fecha < b.fecha ? -1 : 1; });
    var vig = todas.filter(function (r) { return r.vigente === "sí"; }).length;
    $("actualizado").textContent = ultima
      ? "Actualizado el " + fechaLarga(ultima) + " · " + nf.format(vig) + " convocatorias vigentes"
      : "Sin datos";
    leerHash();
    montarControles();
    pintar();
    var rt, anchoPrevio = innerWidth;
    addEventListener("resize", function () {
      if (innerWidth === anchoPrevio) return;   // en el móvil, la barra del navegador cambia solo el alto
      anchoPrevio = innerWidth;
      clearTimeout(rt); rt = setTimeout(pintar, 150);
    });
    addEventListener("hashchange", function () { leerHash(); pintar(); });
  }).catch(function (e) {
    $("actualizado").textContent = "No se pudieron cargar los datos (" + e.message + ").";
  });
})();
