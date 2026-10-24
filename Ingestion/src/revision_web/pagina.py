"""
La página, entera: HTML, CSS y JS en un archivo
===============================================

Sin build, sin node, sin dependencias. El asistente tiene su frontend en React
porque lo usa gente de afuera y se despliega; esto lo abre una persona en su
máquina para mirar recortes y apretar cuatro botones, y meterle una cadena de
build sería ponerle una barrera a la herramienta que existe justamente para
que revisar no cueste trabajo. Mismo criterio —y misma paleta— que el panel de
telemetría de la API.

Lo que la pantalla tiene que lograr es una sola cosa: que el recorte y su
descripción se vean al mismo tiempo. Todo lo demás —la lista de documentos, el
log, el diff— está alrededor de eso.
"""

_ESTILO = """
:root{
  --bg:#0a0e13; --sup:#121821; --sup2:#1a2330; --sup3:#212c3a;
  --bor:#262f3b; --bor2:#323d4d;
  --tex:#e6edf3; --mut:#8b98a5; --mut2:#5b6b7a;
  --ace:#f59e0b; --ace-soft:#f59e0b1a;
  --ok:#34d399; --mal:#f87171; --info:#60a5fa; --vio:#a78bfa;
  --r:10px;
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0; background:
    radial-gradient(1200px 500px at 15% -8%, #1a233055, transparent 60%),
    radial-gradient(900px 400px at 92% -12%, #f59e0b12, transparent 55%),
    var(--bg);
  color:var(--tex);
  font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}

/* ---------- encabezado ---------- */
header{
  padding:.85rem 1.3rem; border-bottom:1px solid var(--bor);
  background:linear-gradient(180deg,#121821f2,#121821b0); backdrop-filter:blur(8px);
  position:sticky; top:0; z-index:30;
}
.titulo{display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap}
h1{margin:0;font-size:1.1rem;font-weight:650;letter-spacing:-.01em}
h1 .punto{color:var(--ace)}
header .sub{color:var(--mut);font-size:.8rem}
header .revisor{margin-left:auto;color:var(--mut2);font-size:.76rem}

/* ---------- barra de trabajo ---------- */
#trabajo{display:none;margin-top:.7rem;border:1px solid var(--bor);
  border-radius:var(--r);background:var(--sup);overflow:hidden}
#trabajo.visible{display:block}
#trabajo .barra{display:flex;align-items:center;gap:.6rem;padding:.45rem .7rem;
  background:var(--sup2)}
#trabajo .et{font-weight:600;font-size:.86rem}
#trabajo .est{font-size:.74rem;text-transform:uppercase;letter-spacing:.06em}
#trabajo.corriendo .est{color:var(--ace)}
#trabajo.ok .est{color:var(--ok)}
#trabajo.error .est,#trabajo.cancelado .est{color:var(--mal)}
#trabajo .der{margin-left:auto;display:flex;gap:.45rem;align-items:center}
#log{max-height:0;overflow:auto;transition:max-height .18s;background:#0a0e13;
  font-size:.78rem;line-height:1.5;padding:0 .7rem;white-space:pre-wrap;
  word-break:break-word;color:var(--mut)}
#log.abierto{max-height:290px;padding:.6rem .7rem}
#log .err{color:var(--mal)}

/* ---------- disposición ---------- */
main{display:grid;grid-template-columns:272px minmax(0,1fr);gap:1.1rem;
  padding:1.1rem 1.3rem 3rem;max-width:1680px;margin:0 auto;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}

/* ---------- documentos ---------- */
/* Con scroll propio: la lista es más alta que la ventana, y un sticky sin
   esto deja los últimos documentos fuera de alcance. */
aside{position:sticky;top:5.2rem;max-height:calc(100vh - 6.5rem);overflow:auto;
  padding-right:.2rem}
aside h2,.seccion{margin:0 0 .5rem;font-size:.7rem;color:var(--mut2);
  text-transform:uppercase;letter-spacing:.09em;font-weight:650}
.doc{display:block;width:100%;text-align:left;cursor:pointer;margin-bottom:.3rem;
  background:var(--sup);border:1px solid var(--bor);border-radius:var(--r);
  padding:.5rem .65rem;color:var(--tex);font:inherit;transition:.13s}
.doc:hover{border-color:var(--bor2);background:var(--sup2)}
.doc[aria-pressed="true"]{border-color:var(--ace);background:var(--ace-soft)}
.doc .n{display:block;font-size:.83rem;font-weight:560;line-height:1.3;
  overflow-wrap:anywhere}
.doc .m{display:flex;gap:.4rem;align-items:center;margin-top:.22rem;
  font-size:.72rem;color:var(--mut2)}

/* ---------- insignias ---------- */
.badge{display:inline-block;border-radius:999px;padding:.05rem .5rem;
  font-size:.7rem;font-weight:620;white-space:nowrap}
.b-pendiente{background:#f59e0b1f;color:var(--ace)}
.b-aprobada{background:#34d39922;color:var(--ok)}
.b-rechazada{background:#f8717122;color:var(--mal)}
.b-neutro,.b-sinregistrar,.b-sintrocear{background:#8b98a51a;color:var(--mut2)}
.b-ok{background:#34d39922;color:var(--ok)}
.b-corregido{background:#60a5fa22;color:var(--info)}
.b-redescribir,.b-editar{background:#f59e0b1f;color:var(--ace)}
.b-descartar{background:#f8717122;color:var(--mal)}

/* ---------- botones ---------- */
button{font:inherit}
.btn{background:var(--sup2);border:1px solid var(--bor);border-radius:8px;
  padding:.3rem .7rem;color:var(--tex);cursor:pointer;transition:.13s;
  font-size:.8rem}
.btn:hover:not(:disabled){border-color:var(--bor2);background:var(--sup3)}
.btn:disabled{opacity:.45;cursor:default}
.btn.p{background:var(--ace);color:#0a0e13;border-color:transparent;font-weight:650}
.btn.p:hover:not(:disabled){filter:brightness(1.1)}
.btn.mal{color:var(--mal)}
.btn.chico{padding:.17rem .5rem;font-size:.74rem}

/* ---------- cabecera de la corrida ---------- */
.cabecera{background:var(--sup);border:1px solid var(--bor);border-radius:var(--r);
  padding:.75rem .9rem;margin-bottom:.9rem}
.cabecera .l1{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.cabecera h2{margin:0;font-size:1rem;font-weight:620;overflow-wrap:anywhere}
.cabecera select{background:var(--sup2);color:var(--tex);border:1px solid var(--bor);
  border-radius:7px;padding:.25rem .45rem;font:inherit;font-size:.78rem}
.cuentas{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.55rem}
.chip{background:var(--sup2);border:1px solid var(--bor);border-radius:999px;
  padding:.1rem .6rem;font-size:.74rem;color:var(--mut);
  font-variant-numeric:tabular-nums}
.chip b{color:var(--tex);font-weight:620}
.chip.al{border-color:#f59e0b55;color:var(--ace)}
.acciones{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.65rem;
  padding-top:.65rem;border-top:1px solid var(--bor)}
.acciones .sep{margin-left:auto}

/* ---------- tarjetas de chunk ---------- */
.chunk{background:var(--sup);border:1px solid var(--bor);border-radius:var(--r);
  margin-bottom:.75rem;overflow:hidden}
.chunk.v-descartar{opacity:.62}
.chunk.v-ok{border-color:#34d39933}
.chunk.v-corregido{border-color:#60a5fa44}
.chunk.v-redescribir,.chunk.v-editar{border-color:#f59e0b55}
.chunk .cab{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap;
  padding:.45rem .7rem;background:var(--sup2);border-bottom:1px solid var(--bor)}
.chunk .cab code{font-size:.78rem;color:var(--tex)}
.chunk .cab .tipo{font-size:.72rem;color:var(--mut2)}
.chunk .cab .enc{font-size:.74rem;color:var(--mut);overflow-wrap:anywhere;
  max-width:38ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chunk .cab .bot{margin-left:auto;display:flex;gap:.3rem;flex-wrap:wrap}
.cuerpo{display:grid;grid-template-columns:minmax(0,420px) minmax(0,1fr);
  gap:.85rem;padding:.75rem}
.cuerpo.solotexto{grid-template-columns:1fr}
@media(max-width:1100px){.cuerpo{grid-template-columns:1fr}}
.cuerpo img{width:100%;border-radius:8px;background:#fff;cursor:zoom-in;
  border:1px solid var(--bor)}
.texto{margin:0;white-space:pre-wrap;word-break:break-word;font-size:.8rem;
  line-height:1.6;color:var(--tex)}
.texto.recortado{max-height:9.5rem;overflow:hidden;
  mask-image:linear-gradient(#000 60%,transparent)}
.mas{margin-top:.3rem}
textarea.edicion{width:100%;min-height:230px;background:#0a0e13;color:var(--tex);
  border:1px solid var(--ace);border-radius:8px;padding:.6rem;resize:vertical;
  outline:none;font-family:ui-monospace,Menlo,monospace;font-size:.8rem;
  line-height:1.6}
.pie{display:flex;gap:.5rem;align-items:center;padding:.4rem .7rem;
  border-top:1px solid var(--bor);font-size:.75rem;color:var(--mut2)}
.pie input{flex:1;background:var(--sup2);border:1px solid var(--bor);
  border-radius:7px;padding:.25rem .5rem;color:var(--tex);font:inherit;
  font-size:.76rem;outline:none}
.pie input:focus{border-color:var(--bor2)}

/* ---------- propuesta de relectura ---------- */
.propuesta{margin:0 .7rem .7rem;border:1px solid #60a5fa55;border-radius:8px;
  background:#60a5fa0d;overflow:hidden}
.propuesta .t{padding:.4rem .6rem;font-size:.75rem;color:var(--info);
  font-weight:620;border-bottom:1px solid #60a5fa33}
.lados{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;padding:.6rem}
@media(max-width:900px){.lados{grid-template-columns:1fr}}
.lado h4{margin:0 0 .25rem;font-size:.7rem;color:var(--mut2);
  text-transform:uppercase;letter-spacing:.07em}
.lado pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:.76rem;
  line-height:1.55;max-height:16rem;overflow:auto}

/* ---------- diff ---------- */
.diff{background:var(--sup);border:1px solid var(--bor);border-radius:var(--r);
  padding:.8rem;margin-bottom:.9rem}
.diff .fila{border-top:1px solid var(--bor);padding-top:.55rem;margin-top:.55rem}

/* ---------- varios ---------- */
.vacio{color:var(--mut);padding:2.5rem 1rem;text-align:center}
.cargando{display:inline-block;width:11px;height:11px;border:2px solid var(--bor2);
  border-top-color:var(--ace);border-radius:50%;animation:g .7s linear infinite}
@keyframes g{to{transform:rotate(360deg)}}
#lupa{position:fixed;inset:0;background:#0a0e13ee;z-index:60;display:none;
  align-items:center;justify-content:center;cursor:zoom-out;padding:1rem}
#lupa.visible{display:flex}
#lupa img{max-width:100%;max-height:100%;background:#fff;border-radius:8px}
#aviso{position:fixed;left:50%;bottom:1.1rem;transform:translateX(-50%);
  background:var(--sup3);border:1px solid var(--bor2);border-radius:999px;
  padding:.4rem 1rem;font-size:.8rem;z-index:70;opacity:0;transition:.2s;
  pointer-events:none;max-width:90vw}
#aviso.visible{opacity:1}
#aviso.mal{border-color:#f8717166;color:var(--mal)}
"""

_SCRIPT = r"""
const $ = (s, r) => (r || document).querySelector(s);
const E = (t, p) => Object.assign(document.createElement(t), p || {});
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

let estado = {
  docs: [], veredictos: {}, doc: null, corrida: null, datos: null,
  verTexto: false, propuestas: {}, lineasVistas: 0, sondeo: null,
};

function aviso(texto, mal) {
  const a = $('#aviso');
  a.textContent = texto;
  a.className = 'visible' + (mal ? ' mal' : '');
  clearTimeout(a._t);
  a._t = setTimeout(() => { a.className = ''; }, mal ? 6000 : 2800);
}

async function pedir(url, opts) {
  const r = await fetch(url, opts);
  let cuerpo = null;
  try { cuerpo = await r.json(); } catch (e) { /* 500 sin json */ }
  if (!r.ok) {
    const m = (cuerpo && (cuerpo.motivo || cuerpo.error)) || ('HTTP ' + r.status);
    const err = new Error(m); err.cuerpo = cuerpo; err.status = r.status; throw err;
  }
  return cuerpo;
}

const enviar = (url, datos) => pedir(url, {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify(datos),
});

/* ------------------------------------------------------------ documentos */

async function cargarDocumentos() {
  const d = await pedir('/api/documentos');
  estado.docs = d.documentos;
  estado.veredictos = d.veredictos;
  $('#revisor').textContent = 'revisando como ' + d.revisor;
  pintarDocumentos();
}

function pintarDocumentos() {
  const cont = $('#docs');
  cont.textContent = '';
  for (const doc of estado.docs) {
    const b = E('button', {className: 'doc'});
    b.setAttribute('aria-pressed', String(doc.nombre === estado.doc));
    b.innerHTML = '<span class="n">' + esc(doc.nombre) + '</span>' +
      '<span class="m"><span class="badge b-' + esc(doc.estado.replace(' ', '')) +
      '">' + esc(doc.estado) + '</span>' +
      (doc.corridas.length ? '<span>' + doc.corridas.length + ' corrida' +
        (doc.corridas.length > 1 ? 's' : '') + '</span>' : '') + '</span>';
    b.onclick = () => abrir(doc.nombre, null);
    cont.appendChild(b);
  }
}

/* --------------------------------------------------------------- corrida */

async function abrir(nombre, corrida) {
  estado.doc = nombre;
  estado.corrida = corrida;
  pintarDocumentos();
  const doc = estado.docs.find(d => d.nombre === nombre);
  if (doc && !doc.corridas.length) { pintarSinCorridas(doc); return; }
  $('#panel').innerHTML = '<div class="vacio"><span class="cargando"></span></div>';
  try {
    estado.datos = await pedir('/api/corrida/' + encodeURIComponent(nombre) +
      (corrida ? '?corrida=' + encodeURIComponent(corrida) : ''));
  } catch (e) { $('#panel').innerHTML = '<div class="vacio">' + esc(e.message) + '</div>'; return; }
  estado.corrida = estado.datos.corrida;
  pintar();
}

function pintarSinCorridas(doc) {
  estado.datos = null;
  $('#panel').innerHTML =
    '<div class="cabecera"><div class="l1"><h2>' + esc(doc.nombre) + '</h2>' +
    '<span class="badge b-neutro">sin trocear</span></div>' +
    '<div class="cuentas"><span class="chip">' + esc(doc.relativa || '—') + '</span>' +
    '<span class="chip">' + (doc.bytes / 1048576).toFixed(1) + ' MB</span></div>' +
    '<div class="acciones"><button class="btn p" id="btn-trocear">Trocear</button>' +
    '<span class="chip">trocea y para: no gasta embeddings ni toca el índice</span>' +
    '</div></div>';
  $('#btn-trocear').onclick = () => trocear(doc);
}

function pintar() {
  const d = estado.datos, r = d.resumen;
  const doc = estado.docs.find(x => x.nombre === d.documento) || {};
  const panel = $('#panel');
  panel.textContent = '';

  const cab = E('div', {className: 'cabecera'});
  cab.innerHTML =
    '<div class="l1"><h2>' + esc(d.documento) + '</h2>' +
    '<span class="badge b-' + esc(d.estado.replace(' ', '')) + '">' + esc(d.estado) + '</span>' +
    '<select id="sel-corrida">' + d.corridas.map(c =>
      '<option value="' + esc(c) + '"' + (c === d.corrida ? ' selected' : '') + '>' +
      esc(c) + (c === d.ultima_aprobada ? ' · aprobada' : '') + '</option>').join('') +
    '</select></div>' +
    '<div class="cuentas">' +
      '<span class="chip"><b>' + r.total + '</b> chunks</span>' +
      '<span class="chip"><b>' + r.visuales + '</b> figuras y tablas</span>' +
      '<span class="chip"><b>' + r.revisados + '</b> con veredicto</span>' +
      (r.sin_revisar_visuales ? '<span class="chip al"><b>' + r.sin_revisar_visuales +
        '</b> figuras sin mirar</span>' : '') +
      (r.pendientes ? '<span class="chip al"><b>' + r.pendientes +
        '</b> marcados para rehacer</span>' : '') +
      (r.corregidos ? '<span class="chip"><b>' + r.corregidos + '</b> corregidos</span>' : '') +
      (r.descartados ? '<span class="chip"><b>' + r.descartados + '</b> descartados</span>' : '') +
    '</div>' +
    '<div class="acciones">' +
      '<button class="btn p" id="b-aprobar">Aprobar</button>' +
      '<button class="btn mal" id="b-rechazar">Rechazar</button>' +
      '<button class="btn" id="b-indexar">Indexar</button>' +
      '<button class="btn" id="b-diff">Comparar con la aprobada</button>' +
      '<button class="btn sep" id="b-trocear">Volver a trocear</button>' +
      '<button class="btn" id="b-texto">' +
        (estado.verTexto ? 'Ocultar el texto' : 'Ver también el texto') + '</button>' +
    '</div>';
  panel.appendChild(cab);

  $('#sel-corrida').onchange = e => abrir(d.documento, e.target.value);
  $('#b-aprobar').onclick = () => decidir('aprobada');
  $('#b-rechazar').onclick = () => decidir('rechazada');
  $('#b-indexar').onclick = () => indexar();
  $('#b-diff').onclick = () => verDiff();
  $('#b-trocear').onclick = () => trocear(doc);
  $('#b-texto').onclick = () => { estado.verTexto = !estado.verTexto; pintar(); };

  const visuales = d.chunks.filter(c => c.visual);
  const textos = d.chunks.filter(c => !c.visual);

  if (visuales.length) {
    panel.appendChild(E('h3', {className: 'seccion',
      textContent: 'Figuras y tablas (' + visuales.length + ')'}));
    visuales.forEach(c => panel.appendChild(tarjeta(c)));
  }
  if (estado.verTexto) {
    panel.appendChild(E('h3', {className: 'seccion',
      textContent: 'Texto (' + textos.length + ')'}));
    textos.forEach(c => panel.appendChild(tarjeta(c)));
  } else if (textos.length) {
    panel.appendChild(E('p', {className: 'vacio',
      textContent: textos.length + ' chunks de texto, ocultos'}));
  }
}

/* -------------------------------------------------------------- tarjetas */

function tarjeta(c) {
  const clave = c.pagina + ':' + c.chunk_id;
  const art = E('article', {className: 'chunk' + (c.veredicto ? ' v-' + c.veredicto : '')});
  art.dataset.clave = clave;

  const cab = E('div', {className: 'cab'});
  cab.innerHTML = '<code>p' + esc(c.pagina) + ':' + esc(c.chunk_id) + '</code>' +
    '<span class="tipo">' + esc(c.tipo) + '</span>' +
    (c.encabezado ? '<span class="enc">' + esc(c.encabezado) + '</span>' : '') +
    (c.veredicto ? '<span class="badge b-' + esc(c.veredicto) + '">' +
      esc(c.veredicto) + '</span>' : '');
  const bot = E('div', {className: 'bot'});
  bot.appendChild(boton('Entra así', 'ok', () => veredicto(c, 'ok')));
  if (c.imagen) bot.appendChild(boton('Releer', 'releer', () => releer(c)));
  bot.appendChild(boton('Editar', 'editar', () => editar(art, c)));
  bot.appendChild(boton('Descartar', 'descartar', () => veredicto(c, 'descartar')));
  cab.appendChild(bot);
  art.appendChild(cab);

  const cuerpo = E('div', {className: 'cuerpo' + (c.imagen ? '' : ' solotexto')});
  if (c.imagen) {
    const img = E('img', {src: c.imagen, loading: 'lazy', alt: 'recorte ' + clave});
    img.onclick = () => lupa(c.imagen);
    cuerpo.appendChild(img);
  }
  const caja = E('div');
  const texto = c.texto_revisado || c.texto;
  const pre = E('pre', {className: 'texto', textContent: texto});
  caja.appendChild(pre);
  if (texto.length > 700) {
    pre.classList.add('recortado');
    const mas = boton('Ver todo', '', () => {
      pre.classList.toggle('recortado');
      mas.textContent = pre.classList.contains('recortado') ? 'Ver todo' : 'Ver menos';
    });
    mas.classList.add('mas');
    caja.appendChild(mas);
  }
  if (c.texto_revisado) {
    caja.appendChild(E('p', {className: 'pie', style: 'padding-left:0;border:0',
      textContent: 'texto corregido en la revisión; el original queda en el JSON del troceo'}));
  }
  cuerpo.appendChild(caja);
  art.appendChild(cuerpo);

  const prop = estado.propuestas[clave];
  if (prop) art.appendChild(bloquePropuesta(c, prop));

  const pie = E('div', {className: 'pie'});
  const nota = E('input', {placeholder: 'nota (qué está mal, qué dice el original…)',
    value: c.nota});
  nota.onchange = () => veredicto(c, c.veredicto || 'ok', nota.value);
  pie.appendChild(nota);
  if (c.quien) pie.appendChild(E('span', {textContent: c.quien}));
  art.appendChild(pie);
  return art;
}

function boton(texto, clase, fn) {
  const b = E('button', {className: 'btn chico', textContent: texto});
  b.onclick = fn;
  return b;
}

function bloquePropuesta(c, p) {
  const caja = E('div', {className: 'propuesta'});
  if (!p.hay) {
    caja.innerHTML = '<div class="t">La relectura no devolvió nada: ' +
      esc(p.motivo || 'sin motivo') + '</div>';
    return caja;
  }
  caja.innerHTML = '<div class="t">Propuesta de la relectura' +
    (p.en_disputa && p.en_disputa.length ? ' · no se pudo leer con seguridad: ' +
      esc(p.en_disputa.join(', ')) : '') + '</div>' +
    '<div class="lados">' +
      '<div class="lado"><h4>como está</h4><pre>' + esc(p.antes) + '</pre></div>' +
      '<div class="lado"><h4>como quedaría</h4><pre>' + esc(p.ahora) + '</pre></div>' +
    '</div>';
  const pie = E('div', {className: 'pie'});
  const aceptar = E('button', {className: 'btn p chico', textContent: 'Aplicar'});
  aceptar.onclick = async () => {
    await veredicto(c, 'corregido', '', p.ahora);
    delete estado.propuestas[c.pagina + ':' + c.chunk_id];
    abrir(estado.doc, estado.corrida);
  };
  const tirar = E('button', {className: 'btn chico', textContent: 'Descartar la propuesta'});
  tirar.onclick = () => {
    delete estado.propuestas[c.pagina + ':' + c.chunk_id];
    pintar();
  };
  pie.appendChild(aceptar); pie.appendChild(tirar);
  caja.appendChild(pie);
  return caja;
}

function editar(art, c) {
  const caja = $('.cuerpo > div:last-child', art);
  caja.textContent = '';
  const ta = E('textarea', {className: 'edicion', value: c.texto_revisado || c.texto});
  caja.appendChild(ta);
  const pie = E('div', {className: 'pie', style: 'padding:.5rem 0 0;border:0'});
  const guardar = E('button', {className: 'btn p chico', textContent: 'Guardar'});
  guardar.onclick = async () => {
    await veredicto(c, 'corregido', '', ta.value);
    abrir(estado.doc, estado.corrida);
  };
  const cancelar = E('button', {className: 'btn chico', textContent: 'Cancelar'});
  cancelar.onclick = () => abrir(estado.doc, estado.corrida);
  pie.appendChild(guardar); pie.appendChild(cancelar);
  caja.appendChild(pie);
  ta.focus();
}

/* ------------------------------------------------------------- decisiones */

async function veredicto(c, cual, nota, textoRevisado) {
  const datos = {documento: estado.doc, corrida: estado.corrida,
    pagina: c.pagina, chunk_id: c.chunk_id, veredicto: cual,
    nota: nota !== undefined ? nota : c.nota};
  if (textoRevisado !== undefined) datos.texto_revisado = textoRevisado;
  try {
    await enviar('/api/veredicto', datos);
    aviso('p' + c.pagina + ':' + c.chunk_id + ' → ' + cual);
    if (textoRevisado === undefined) await abrir(estado.doc, estado.corrida);
  } catch (e) { aviso(e.message, true); }
}

async function decidir(cual) {
  if (cual === 'rechazada' &&
      !confirm('Rechazar la corrida: no se va a indexar hasta trocearla de nuevo.')) return;
  try {
    const r = await enviar('/api/decidir',
      {documento: estado.doc, corrida: estado.corrida, estado: cual});
    aviso(cual === 'aprobada'
      ? 'Aprobada' + (r.descartados ? ', con ' + r.descartados + ' descartados' : '') +
        '. Ya se puede indexar.'
      : 'Rechazada. No se indexa.');
  } catch (e) {
    const p = e.cuerpo && e.cuerpo.pendientes;
    aviso(e.message + (p ? ': ' + p.map(x => 'p' + x.pagina + ':' + x.chunk_id).join(', ') : ''),
      true);
  }
  await cargarDocumentos();
  await abrir(estado.doc, estado.corrida);
}

async function verDiff() {
  const d = await pedir('/api/corrida/' + encodeURIComponent(estado.doc) +
    '/diff?corrida=' + encodeURIComponent(estado.corrida));
  const caja = E('div', {className: 'diff'});
  if (!d.hay) {
    caja.innerHTML = '<h3 class="seccion">Comparación</h3><p>' + esc(d.motivo) + '</p>';
  } else {
    caja.innerHTML = '<h3 class="seccion">Contra la aprobada ' + esc(d.previa) + '</h3>' +
      '<div class="cuentas"><span class="chip"><b>' + d.nuevos.length + '</b> nuevos</span>' +
      '<span class="chip"><b>' + d.perdidos.length + '</b> perdidos</span>' +
      '<span class="chip"><b>' + d.cambiados.length + '</b> cambiados</span></div>' +
      d.perdidos.map(k => '<div class="fila">✗ desapareció <code>p' + esc(k.pagina) +
        ':' + esc(k.chunk_id) + '</code></div>').join('') +
      d.nuevos.map(k => '<div class="fila">+ nuevo <code>p' + esc(k.pagina) +
        ':' + esc(k.chunk_id) + '</code></div>').join('') +
      d.cambiados.map(k => '<div class="fila">~ cambió <code>p' + esc(k.pagina) + ':' +
        esc(k.chunk_id) + '</code><div class="lados">' +
        '<div class="lado"><h4>antes</h4><pre>' + esc(k.antes) + '</pre></div>' +
        '<div class="lado"><h4>ahora</h4><pre>' + esc(k.ahora) + '</pre></div></div></div>').join('');
  }
  const panel = $('#panel');
  const viejo = $('.diff', panel);
  if (viejo) viejo.remove();
  panel.insertBefore(caja, panel.children[1] || null);
  caja.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

/* --------------------------------------------------------------- trabajos */

async function trocear(doc) {
  if (!doc || !doc.filtro) { aviso('ese documento ya no está en raw_data', true); return; }
  if (!confirm('Trocear "' + doc.nombre + '".\n\nEs la etapa cara: pasa cada página ' +
      'por el modelo de visión. Trocea y para, sin indexar nada.')) return;
  try {
    await enviar('/api/procesar', {documento: doc.nombre, filtro: doc.filtro});
    arrancarSondeo(true);
  } catch (e) { aviso(e.message, true); }
}

async function indexar() {
  const doc = estado.docs.find(x => x.nombre === estado.doc) || {};
  if (estado.datos.estado !== 'aprobada' &&
      !confirm('Esta corrida no está aprobada, así que el pipeline la va a saltear.' +
        '\n\n¿Lanzar igual?')) return;
  try {
    await enviar('/api/indexar',
      {documento: estado.doc, filtro: doc.filtro || estado.doc});
    arrancarSondeo(true);
  } catch (e) { aviso(e.message, true); }
}

async function releer(c) {
  if (!confirm('Releer la figura con el modelo de visión.\n\nSon varias llamadas: ' +
      'se transcribe dos veces con encuadres distintos y recién después se describe.')) return;
  try {
    await enviar('/api/releer', {documento: estado.doc, corrida: estado.corrida,
      pagina: c.pagina, chunk_id: c.chunk_id});
    arrancarSondeo(true);
  } catch (e) { aviso(e.message, true); }
}

function arrancarSondeo(abrirLog) {
  estado.lineasVistas = 0;
  $('#log').textContent = '';
  if (abrirLog) $('#log').classList.add('abierto');
  if (estado.sondeo) clearInterval(estado.sondeo);
  sondear();
  estado.sondeo = setInterval(sondear, 1200);
}

async function sondear() {
  let t;
  try { t = await pedir('/api/trabajo?desde=' + estado.lineasVistas); }
  catch (e) { return; }
  const caja = $('#trabajo');
  if (!t.hay) { caja.className = ''; return; }

  caja.className = 'visible ' + t.estado;
  $('#t-etiqueta').textContent = t.etiqueta;
  $('#t-estado').textContent = t.estado;
  $('#t-spin').style.display = t.estado === 'corriendo' ? '' : 'none';
  $('#t-cancelar').disabled = t.estado !== 'corriendo';

  const log = $('#log');
  const abajo = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  if (t.perdidas) log.appendChild(E('div', {className: 'err',
    textContent: '… ' + t.perdidas + ' líneas que ya no están en el búfer'}));
  for (const l of t.lineas) log.appendChild(document.createTextNode(l + '\n'));
  estado.lineasVistas = t.total;
  if (abajo) log.scrollTop = log.scrollHeight;

  if (t.estado === 'corriendo' && !estado.sondeo) {
    // Hay un trabajo de antes de abrir la pestaña: se sigue desde acá.
    estado.sondeo = setInterval(sondear, 1200);
  }
  if (t.estado !== 'corriendo') {
    clearInterval(estado.sondeo); estado.sondeo = null;
    if (t.estado === 'error') aviso(t.etiqueta + ': ' + (t.error || 'falló'), true);
    if (t.clase === 'releer' && t.estado === 'ok' && t.resultado) {
      estado.propuestas[t.resultado.pagina + ':' + t.resultado.chunk_id] = t.resultado;
    }
    await cargarDocumentos();
    const ctx = t.contexto || {};
    if (ctx.documento) await abrir(ctx.documento, ctx.corrida || null);
  }
}

/* ------------------------------------------------------------------ varios */

function lupa(src) {
  const l = $('#lupa');
  $('img', l).src = src;
  l.classList.add('visible');
}

window.addEventListener('DOMContentLoaded', () => {
  $('#lupa').onclick = () => $('#lupa').classList.remove('visible');
  $('#t-log').onclick = () => $('#log').classList.toggle('abierto');
  $('#t-cancelar').onclick = async () => {
    try { await enviar('/api/trabajo/cancelar', {}); aviso('cancelando…'); }
    catch (e) { aviso(e.message, true); }
  };
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') $('#lupa').classList.remove('visible');
  });
  // Un solo sondeo al abrir: si quedó un trabajo de antes —o terminó con la
  // pestaña cerrada— se engancha ahí, y si está corriendo se arma el
  // intervalo solo. Dos llamadas en paralelo duplicarían el log, porque las
  // dos arrancan con lineasVistas en cero.
  cargarDocumentos().then(sondear);
});
"""


def pagina_html() -> str:
    return (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Revisión de la ingesta</title>"
        "<style>" + _ESTILO + "</style></head><body>"
        "<header>"
        "  <div class='titulo'>"
        "    <h1>Revisión de la ingesta<span class='punto'>.</span></h1>"
        "    <span class='sub'>lo que se troceó, antes de que entre al índice</span>"
        "    <span class='revisor' id='revisor'></span>"
        "  </div>"
        "  <div id='trabajo'>"
        "    <div class='barra'>"
        "      <span class='cargando' id='t-spin'></span>"
        "      <span class='et' id='t-etiqueta'></span>"
        "      <span class='est' id='t-estado'></span>"
        "      <span class='der'>"
        "        <button class='btn chico' id='t-log'>log</button>"
        "        <button class='btn chico mal' id='t-cancelar'>cancelar</button>"
        "      </span>"
        "    </div>"
        "    <div id='log'></div>"
        "  </div>"
        "</header>"
        "<main>"
        "  <aside><h2>Documentos</h2><div id='docs'></div></aside>"
        "  <section id='panel'><div class='vacio'>Elegí un documento.</div></section>"
        "</main>"
        "<div id='lupa'><img alt='recorte ampliado'></div>"
        "<div id='aviso'></div>"
        "<script>" + _SCRIPT + "</script></body></html>"
    )
