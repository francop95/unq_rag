"""
Panel web de la telemetría
==========================

Una página para mirar los datos sin SSH, sin bajar archivos y sin instalar un
cliente de SQLite. Entra por el mismo nginx que la demo, así que la protege la
misma contraseña.

Trae las consultas de `consultas.py` listas para un clic y una caja de SQL para
lo que no esté previsto. La ejecución es de solo lectura: ver `store.consultar`.
"""

import html
import json

from .consultas import CONSULTAS

_ESTILO = """
:root{--bg:#0a0e13;--sup:#121821;--sup2:#1a2330;--bor:#262f3b;--tex:#e6edf3;
      --mut:#8b98a5;--ace:#f59e0b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tex);
     font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:1rem 1.25rem;border-bottom:1px solid var(--bor);background:var(--sup)}
h1{margin:0;font-size:1.05rem;font-weight:600}
header p{margin:.25rem 0 0;color:var(--mut);font-size:.85rem}
main{padding:1.25rem;max-width:1400px;margin:0 auto}
.guardadas{display:flex;flex-wrap:wrap;gap:.5rem;margin-bottom:1rem}
.guardadas button{background:var(--sup2);color:var(--tex);border:1px solid var(--bor);
     border-radius:999px;padding:.35rem .8rem;cursor:pointer;font:inherit;font-size:.85rem}
.guardadas button:hover{border-color:var(--ace)}
textarea{width:100%;min-height:95px;background:var(--sup);color:var(--tex);
     border:1px solid var(--bor);border-radius:8px;padding:.7rem;
     font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;resize:vertical}
.acciones{display:flex;gap:.6rem;align-items:center;margin:.6rem 0 1rem}
button.correr{background:var(--ace);color:#0a0e13;border:0;border-radius:8px;
     padding:.5rem 1.1rem;font:inherit;font-weight:600;cursor:pointer}
button.correr:disabled{opacity:.6;cursor:default}
.ayuda{color:var(--mut);font-size:.82rem}
#salida{overflow-x:auto;border:1px solid var(--bor);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{text-align:left;padding:.45rem .7rem;border-bottom:1px solid var(--bor);
      white-space:nowrap;max-width:460px;overflow:hidden;text-overflow:ellipsis}
th{background:var(--sup2);position:sticky;top:0;font-weight:600}
tr:hover td{background:#ffffff08}
.error{color:#f87171;padding:.8rem}
.meta{color:var(--mut);font-size:.82rem;margin:.5rem 0}
"""


def _script(consultas_json: str) -> str:
    return """
const CONSULTAS = __CONSULTAS__;
const caja = document.getElementById('sql');
const salida = document.getElementById('salida');
const meta = document.getElementById('meta');

document.querySelectorAll('.guardadas button').forEach(b => {
  b.onclick = () => { caja.value = CONSULTAS[b.dataset.id].trim(); correr(); };
});
document.getElementById('correr').onclick = correr;
// Ctrl/Cmd+Enter para correr sin sacar las manos del teclado.
caja.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') correr();
});

async function correr() {
  const boton = document.getElementById('correr');
  boton.disabled = true;
  meta.textContent = 'Consultando…';
  salida.innerHTML = '';
  try {
    const res = await fetch('telemetria/consulta', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sql: caja.value}),
    });
    const d = await res.json();
    if (!res.ok || d.error) {
      salida.innerHTML = '<p class="error">' + (d.error || ('HTTP ' + res.status)) + '</p>';
      meta.textContent = '';
      return;
    }
    if (!d.filas.length) { meta.textContent = 'Sin resultados.'; return; }
    const th = d.columnas.map(c => '<th>' + esc(c) + '</th>').join('');
    const tr = d.filas.map(f =>
      '<tr>' + f.map(v => '<td title="' + esc(v) + '">' + esc(v) + '</td>').join('') + '</tr>'
    ).join('');
    salida.innerHTML = '<table><thead><tr>' + th + '</tr></thead><tbody>' + tr + '</tbody></table>';
    meta.textContent = d.total + ' filas' + (d.truncado ? ' (se muestran las primeras 500)' : '');
  } catch (e) {
    salida.innerHTML = '<p class="error">' + esc(String(e)) + '</p>';
    meta.textContent = '';
  } finally {
    boton.disabled = false;
  }
}

function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v).replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

correr();
""".replace("__CONSULTAS__", consultas_json)


def pagina_html() -> str:
    """El panel completo, en una sola página sin dependencias externas."""
    botones = "\n".join(
        f'<button data-id="{html.escape(c["id"])}" title="{html.escape(c.get("ayuda",""))}">'
        f'{html.escape(c["titulo"])}</button>'
        for c in CONSULTAS
    )
    consultas_json = json.dumps({c["id"]: c["sql"] for c in CONSULTAS}, ensure_ascii=False)
    inicial = html.escape(CONSULTAS[0]["sql"].strip())

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Telemetría · Asistente del secadero</title>
<style>{_ESTILO}</style></head>
<body>
<header>
  <h1>Telemetría del asistente</h1>
  <p>Qué se preguntó, qué se recuperó y qué opinó quien preguntó. Solo lectura.</p>
</header>
<main>
  <div class="guardadas">{botones}</div>
  <textarea id="sql" spellcheck="false">{inicial}</textarea>
  <div class="acciones">
    <button class="correr" id="correr">Ejecutar</button>
    <span class="ayuda">Ctrl/Cmd + Enter · solo SELECT · tablas:
      <code>ejecuciones</code>, <code>contextos</code>, <code>feedback</code></span>
  </div>
  <p class="meta" id="meta"></p>
  <div id="salida"></div>
</main>
<script>{_script(consultas_json)}</script>
</body></html>"""
