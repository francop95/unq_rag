"""
Panel web de la telemetría
==========================

Una página para mirar los datos sin SSH, sin bajar archivos y sin instalar un
cliente de SQLite. Entra por el mismo nginx que la demo, así que la protege la
misma contraseña.

Sobre el diseño: la tabla es el producto, no un volcado. Los números van
alineados a la derecha y en monoespaciada para poder compararlos de un vistazo;
los scores llevan una escala de color porque un 80 y un 55 significan cosas muy
distintas y leer el dígito no lo transmite; las columnas de sí/no son insignias
en vez de ceros y unos. Nada de eso es decoración: es lo que convierte una
grilla en algo que se lee.

Las consultas vienen de `consultas.py`, agrupadas por tema y con su descripción
a la vista, para no tener que recordar qué hace cada una.
"""

import html
import json

from .consultas import CONSULTAS, GRUPOS

_ESTILO = """
:root{
  --bg:#0a0e13; --sup:#121821; --sup2:#1a2330; --sup3:#212c3a;
  --bor:#262f3b; --bor2:#323d4d;
  --tex:#e6edf3; --mut:#8b98a5; --mut2:#5b6b7a;
  --ace:#f59e0b; --ace-soft:#f59e0b1a;
  --ok:#34d399; --mal:#f87171; --info:#60a5fa;
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

/* ---------- encabezado ---------- */
header{
  padding:1.1rem 1.5rem .9rem; border-bottom:1px solid var(--bor);
  background:linear-gradient(180deg,#121821f2,#121821b0); backdrop-filter:blur(8px);
  position:sticky; top:0; z-index:20;
}
.titulo{display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap}
h1{margin:0;font-size:1.15rem;font-weight:650;letter-spacing:-.01em}
h1 .punto{color:var(--ace)}
header p{margin:.15rem 0 0;color:var(--mut);font-size:.84rem}

/* ---------- métricas ---------- */
.kpis{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.9rem}
.kpi{
  background:var(--sup2); border:1px solid var(--bor); border-radius:var(--r);
  padding:.55rem .85rem; min-width:118px;
}
.kpi .v{font-size:1.32rem;font-weight:660;line-height:1.15;
        font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.kpi .e{font-size:.72rem;color:var(--mut);text-transform:uppercase;letter-spacing:.055em}
.kpi.bueno .v{color:var(--ok)} .kpi.malo .v{color:var(--mal)} .kpi.acento .v{color:var(--ace)}

main{padding:1.25rem 1.5rem 3rem;max-width:1500px;margin:0 auto}

/* ---------- consultas guardadas ---------- */
.grupos{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));
        gap:.85rem;margin-bottom:1.35rem}
.grupo h2{margin:0 0 .45rem;font-size:.7rem;color:var(--mut2);
          text-transform:uppercase;letter-spacing:.09em;font-weight:650}
.grupo{display:flex;flex-direction:column;gap:.35rem}
.q{
  display:block;width:100%;text-align:left;cursor:pointer;
  background:var(--sup); border:1px solid var(--bor); border-radius:var(--r);
  padding:.55rem .7rem; color:var(--tex); font:inherit; transition:.13s;
}
.q:hover{border-color:var(--bor2);background:var(--sup2);transform:translateY(-1px)}
.q[aria-pressed="true"]{border-color:var(--ace);background:var(--ace-soft)}
.q b{display:block;font-size:.855rem;font-weight:580}
.q span{display:block;font-size:.745rem;color:var(--mut);margin-top:.1rem;line-height:1.35}

/* ---------- editor ---------- */
.editor{background:var(--sup);border:1px solid var(--bor);border-radius:var(--r);
        overflow:hidden;margin-bottom:1rem}
.editor .barra{display:flex;align-items:center;gap:.55rem;padding:.45rem .7rem;
        border-bottom:1px solid var(--bor);background:var(--sup2)}
.editor .barra .t{font-size:.73rem;color:var(--mut);text-transform:uppercase;
        letter-spacing:.07em;font-weight:620}
.editor .barra .tablas{margin-left:auto;font-size:.75rem;color:var(--mut2)}
.editor .barra code{background:var(--sup3);padding:.08rem .34rem;border-radius:5px;
        color:var(--mut);font-size:.72rem}
textarea{
  width:100%;min-height:300px;height:38vh;background:transparent;color:var(--tex);border:0;
  padding:.85rem;resize:vertical;outline:none;
  font:14px/1.7 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  tab-size:2;
}
textarea::selection{background:#f59e0b40}
.acciones{display:flex;gap:.7rem;align-items:center;padding:.55rem .7rem;
          border-top:1px solid var(--bor);background:var(--sup2)}
button.correr{
  background:var(--ace);color:#0a0e13;border:0;border-radius:8px;
  padding:.45rem 1.15rem;font:inherit;font-weight:660;cursor:pointer;transition:.13s;
}
button.correr:hover:not(:disabled){filter:brightness(1.1)}
button.correr:disabled{opacity:.55;cursor:default}
.ayuda{color:var(--mut2);font-size:.775rem}
kbd{background:var(--sup3);border:1px solid var(--bor2);border-bottom-width:2px;
    border-radius:5px;padding:.02rem .3rem;font:inherit;font-size:.72rem;color:var(--mut)}

/* ---------- resultados ---------- */
.meta{display:flex;align-items:center;gap:.6rem;margin:0 0 .55rem;
      color:var(--mut);font-size:.8rem;min-height:1.2rem}
.chip{background:var(--sup2);border:1px solid var(--bor);border-radius:999px;
      padding:.1rem .6rem;font-size:.745rem;font-variant-numeric:tabular-nums}
#salida{border:1px solid var(--bor);border-radius:var(--r);overflow:auto;
        max-height:60vh;min-height:130px;background:var(--sup)}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.845rem}
th,td{text-align:left;padding:.5rem .8rem;border-bottom:1px solid var(--bor);
      white-space:nowrap;max-width:430px;overflow:hidden;text-overflow:ellipsis}
th{background:var(--sup2);position:sticky;top:0;z-index:5;font-weight:620;
   font-size:.73rem;text-transform:uppercase;letter-spacing:.055em;color:var(--mut)}
tbody tr:nth-child(even) td{background:#ffffff04}
tbody tr:hover td{background:var(--ace-soft)}
tbody tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums;
       font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.82rem}
td.vacio{color:var(--mut2)}
.badge{display:inline-block;border-radius:999px;padding:.05rem .5rem;
       font-size:.72rem;font-weight:600}
.badge.si{background:#34d39922;color:var(--ok)}
.badge.no{background:#8b98a51a;color:var(--mut2)}
.score{display:inline-block;border-radius:6px;padding:.05rem .42rem;
       font-variant-numeric:tabular-nums;font-weight:620;font-size:.8rem}
.estado{padding:2.6rem 1rem;text-align:center;color:var(--mut)}
.estado.err{color:var(--mal);white-space:pre-wrap;text-align:left;padding:1rem;
            font-family:ui-monospace,Menlo,monospace;font-size:.82rem}
.cargando{display:inline-block;width:11px;height:11px;border:2px solid var(--bor2);
          border-top-color:var(--ace);border-radius:50%;animation:g .7s linear infinite;
          vertical-align:-1px;margin-right:.4rem}
@keyframes g{to{transform:rotate(360deg)}}

@media(max-width:640px){
  main{padding:1rem .8rem 2rem} header{padding:.9rem .8rem}
  th,td{max-width:210px}
}
"""


def _script(consultas_json: str) -> str:
    return r"""
const CONSULTAS = __CONSULTAS__;
const caja   = document.getElementById('sql');
const salida = document.getElementById('salida');
const meta   = document.getElementById('meta');
const botones = [...document.querySelectorAll('.q')];

botones.forEach(b => b.onclick = () => {
  botones.forEach(o => o.setAttribute('aria-pressed', o === b ? 'true' : 'false'));
  caja.value = CONSULTAS[b.dataset.id].trim();
  correr();
});
document.getElementById('correr').onclick = () => {
  botones.forEach(o => o.setAttribute('aria-pressed', 'false'));
  correr();
};
caja.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); correr(); }
});

// Columnas que se muestran como insignia en vez de 0/1: un "sí" verde se lee de
// un vistazo, un "1" hay que interpretarlo.
const BOOLEANAS = new Set(['mostrado','citado','util','cache','con_historial','desde_cache']);
// Columnas cuyo valor es un score 0-100 y merece escala de color.
const SCORES = new Set(['score','score_max','score_maximo','score_promedio']);

async function correr(){
  const boton = document.getElementById('correr');
  boton.disabled = true;
  meta.innerHTML = '<span class="cargando"></span>Consultando…';
  salida.innerHTML = '';
  try{
    const res = await fetch('telemetria/consulta', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({sql: caja.value}),
    });
    const d = await res.json();
    if(!res.ok || d.error){
      salida.innerHTML = '<div class="estado err">' + esc(d.error || ('HTTP '+res.status)) + '</div>';
      meta.textContent = '';
      return;
    }
    pintar(d);
  }catch(e){
    salida.innerHTML = '<div class="estado err">' + esc(String(e)) + '</div>';
    meta.textContent = '';
  }finally{
    boton.disabled = false;
  }
}

function pintar(d){
  if(!d.filas.length){
    salida.innerHTML = '<div class="estado">Sin resultados para esta consulta.</div>';
    meta.innerHTML = '<span class="chip">0 filas</span>';
    return;
  }
  const th = d.columnas.map(c => '<th>' + esc(c.replace(/_/g,' ')) + '</th>').join('');
  const tr = d.filas.map(f =>
    '<tr>' + f.map((v,i) => celda(v, d.columnas[i])).join('') + '</tr>').join('');
  salida.innerHTML = '<table><thead><tr>'+th+'</tr></thead><tbody>'+tr+'</tbody></table>';
  meta.innerHTML = '<span class="chip">' + d.total + ' fila' + (d.total===1?'':'s') + '</span>'
    + (d.truncado ? '<span class="chip">se muestran las primeras 500</span>' : '')
    + '<span class="chip">' + d.columnas.length + ' columnas</span>';
}

function celda(v, col){
  if(v === null || v === undefined || v === '')
    return '<td class="vacio">—</td>';
  if(BOOLEANAS.has(col)){
    const si = v === 1 || v === true || v === 'sí' || v === 'si';
    return '<td><span class="badge ' + (si?'si':'no') + '">' + (si?'sí':'no') + '</span></td>';
  }
  if(SCORES.has(col) && typeof v === 'number')
    return '<td class="num"><span class="score" style="background:' + fondoScore(v)
         + ';color:' + colorScore(v) + '">' + v.toFixed(1) + '</span></td>';
  if(typeof v === 'number')
    return '<td class="num">' + v + '</td>';
  return '<td title="' + esc(v) + '">' + esc(v) + '</td>';
}

// Escala sobre el rango que estos scores ocupan de verdad (55-85), no sobre
// 0-100: con el rango completo todo queda del mismo color y la escala no informa.
function fondoScore(v){
  if(v >= 75) return '#34d39926';
  if(v >= 65) return '#f59e0b26';
  if(v >= 58) return '#60a5fa1f';
  return '#f8717122';
}
function colorScore(v){
  if(v >= 75) return '#34d399';
  if(v >= 65) return '#f59e0b';
  if(v >= 58) return '#60a5fa';
  return '#f87171';
}

function esc(v){
  if(v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// Las métricas de arriba salen de la misma base, con su propia consulta.
async function kpis(){
  try{
    const res = await fetch('telemetria/consulta', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({sql:
        "SELECT (SELECT COUNT(*) FROM ejecuciones) a,"
      + " (SELECT COUNT(*) FROM feedback) b,"
      + " (SELECT COUNT(*) FROM feedback WHERE util=1) c,"
      + " (SELECT COUNT(*) FROM feedback WHERE util=0) d,"
      + " (SELECT COUNT(*) FROM ejecuciones WHERE n_contextos=0) e,"
      + " (SELECT round(AVG(score_maximo),1) FROM ejecuciones WHERE score_maximo IS NOT NULL) f"}),
    });
    const j = await res.json();
    if(!j.filas || !j.filas.length) return;
    const [a,b,c,d,e,f] = j.filas[0];
    const pct = b ? Math.round(c*100/b) : null;
    document.getElementById('kpis').innerHTML =
        kpi(a, 'consultas')
      + kpi(b, 'con feedback')
      + (pct === null ? '' : kpi(pct + '%', 'sirvieron', pct >= 70 ? 'bueno' : 'malo'))
      + kpi(d, 'no sirvieron', d > 0 ? 'malo' : '')
      + kpi(e, 'sin contexto', e > 0 ? 'malo' : '')
      + kpi(f ?? '—', 'score medio', 'acento');
  }catch(_){ /* el panel sirve igual sin las métricas */ }
}
function kpi(v, e, clase){
  return '<div class="kpi ' + (clase||'') + '"><div class="v">' + esc(v)
       + '</div><div class="e">' + esc(e) + '</div></div>';
}

kpis();
correr();
""".replace("__CONSULTAS__", consultas_json)


def pagina_html() -> str:
    """El panel completo, en una sola página sin dependencias externas."""
    grupos_html = []
    for g in GRUPOS:
        del_grupo = [c for c in CONSULTAS if c.get("grupo") == g]
        if not del_grupo:
            continue
        botones = "\n".join(
            f'<button class="q" data-id="{html.escape(c["id"])}" aria-pressed="false">'
            f'<b>{html.escape(c["titulo"])}</b>'
            f'<span>{html.escape(c.get("ayuda", ""))}</span></button>'
            for c in del_grupo
        )
        grupos_html.append(f'<div class="grupo"><h2>{html.escape(g)}</h2>{botones}</div>')

    consultas_json = json.dumps({c["id"]: c["sql"] for c in CONSULTAS}, ensure_ascii=False)
    inicial = html.escape(CONSULTAS[0]["sql"].strip())

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telemetría · Asistente del secadero</title>
<style>{_ESTILO}</style></head>
<body>
<header>
  <div class="titulo">
    <h1>Telemetría<span class="punto">.</span></h1>
    <p>Qué se preguntó, qué se recuperó y qué opinó quien preguntó</p>
  </div>
  <div class="kpis" id="kpis"></div>
</header>
<main>
  <div class="grupos">{''.join(grupos_html)}</div>

  <div class="editor">
    <div class="barra">
      <span class="t">SQL</span>
      <span class="tablas">
        <code>ejecuciones</code> <code>contextos</code> <code>feedback</code>
      </span>
    </div>
    <textarea id="sql" spellcheck="false">{inicial}</textarea>
    <div class="acciones">
      <button class="correr" id="correr">Ejecutar</button>
      <span class="ayuda"><kbd>Ctrl</kbd>/<kbd>⌘</kbd> + <kbd>Enter</kbd> · solo lectura</span>
    </div>
  </div>

  <p class="meta" id="meta"></p>
  <div id="salida"></div>
</main>
<script>{_script(consultas_json)}</script>
</body></html>"""
