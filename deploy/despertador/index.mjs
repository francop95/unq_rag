/**
 * La puerta: despierta la instancia y después le hace de proxy
 * =============================================================
 *
 * El problema que resuelve: la demo se usa un día de cada cuatro, pero tiene
 * que estar disponible cuando alguien entra. Tenerla prendida siempre cuesta
 * ~21 USD/mes y el 89% de eso es tiempo encendido. Con la instancia apagada y
 * esto adelante, salen ~3,50.
 *
 * Esto es lo único que queda corriendo, y entra en la capa gratuita de Lambda
 * (1M de invocaciones al mes; la demo lleva 148 consultas en 39 días).
 *
 * Por qué proxy y no una redirección
 * ----------------------------------
 * Redirigir es menos código, pero deja al visitante parado en la IP cruda de
 * la instancia. Cuando el apagado por inactividad la baja, un refresh de esa
 * pestaña da "conexión rechazada" en vez de volver a encenderla. Con proxy
 * TODO vuelve a pasar por acá, así que la puerta siempre está, y de paso el
 * sitio pasa a servirse por HTTPS: hoy la contraseña de basic auth viaja en
 * claro hasta la instancia.
 *
 * Por qué la contraseña se valida acá y no solo en nginx
 * ------------------------------------------------------
 * Porque si no, cualquier bot que escanee la URL enciende la instancia, y a
 * las pocas semanas estás pagando lo mismo que antes sin que nadie la use. El
 * chequeo de acá es lo que hace que el ahorro exista. La cabecera se reenvía
 * tal cual a nginx, que vuelve a validarla con su propio .htpasswd, así que
 * al visitante se le pide la clave una sola vez.
 *
 * Por qué cero dependencias npm
 * -----------------------------
 * Las llamadas a EC2 van firmadas a mano con SigV4 (son dos: preguntar el
 * estado y encender). Podría usarse el SDK que trae el runtime, pero eso ata
 * la función a que esa versión del runtime lo siga incluyendo, y sobre todo
 * no se puede probar sin desplegar. Sin dependencias, `pruebas.mjs` corre el
 * handler entero en la máquina de uno antes de subir nada.
 *
 * Por qué response streaming
 * --------------------------
 * Una Lambda normal corta en 6 MB de payload, y el cuerpo binario viaja en
 * base64, o sea que el techo real son ~4,4 MB. Por /media se descarga un PDF
 * de 5,4 MB. Con streaming el techo sube a 20 MB y además el visitante
 * empieza a recibir el archivo sin esperar a que la Lambda lo junte entero.
 */

import { createHash, createHmac, pbkdf2Sync, timingSafeEqual } from "node:crypto";
import http from "node:http";

const REGION = process.env.AWS_REGION || "us-east-1";
const INSTANCIA = process.env.INSTANCE_ID;
const PUERTO = Number(process.env.UPSTREAM_PORT || 80);
const REALM = process.env.AUTH_REALM || "Asistente Tecnico - acceso restringido";
const SAL = process.env.AUTH_SALT || "";
const HASH = process.env.AUTH_HASH || "";
const VUELTAS = Number(process.env.AUTH_ITER || 200000);

// Lo que tarda en estar sirviendo desde apagada: ~22 s de arranque del sistema
// más 20-40 s de aprovisionamiento de EC2. Medido sobre esta misma instancia.
const ESPERA_SEG = 6;

// Dónde queda la credencial del navegador, y por cuánto. Una semana: es una
// demo que se visita cada tantos días y volver a escribir la clave en cada
// visita es la clase de fricción que hace que nadie la mire.
const COOKIE = "unq_rag_acceso";
const COOKIE_SEG = 7 * 24 * 3600;
const RUTA_ENTRAR = "/__entrar";

// Cabeceras que pertenecen a UNA conexión y no se reenvían. Dejar pasar
// `transfer-encoding` o `connection` rompe la respuesta que arma Lambda.
const POR_CONEXION = new Set([
  "connection", "keep-alive", "transfer-encoding", "upgrade",
  "proxy-authenticate", "proxy-authorization", "te", "trailer",
]);

// ------------------------------------------------------------------ SigV4

const sha256 = (dato) => createHash("sha256").update(dato).digest("hex");
const hmac = (clave, dato) => createHmac("sha256", clave).update(dato, "utf8").digest();

/**
 * Una llamada a la Query API de EC2, firmada. Devuelve el XML crudo.
 *
 * Se usa la Query API y no la JSON porque el cuerpo es un form-urlencoded y
 * la respuesta un XML del que acá solo hacen falta dos campos: alcanza con
 * firmar bien y leer dos etiquetas.
 */
async function ec2(accion, params = {}) {
  const host = `ec2.${REGION}.amazonaws.com`;
  const cuerpo = new URLSearchParams(
    { Action: accion, Version: "2016-11-15", ...params }).toString();

  const momento = new Date().toISOString().replace(/[:-]|\.\d{3}/g, "");
  const dia = momento.slice(0, 8);
  const token = process.env.AWS_SESSION_TOKEN;
  const tipo = "application/x-www-form-urlencoded; charset=utf-8";

  const cabeceras = { "content-type": tipo, host, "x-amz-date": momento };
  if (token) cabeceras["x-amz-security-token"] = token;

  const nombres = Object.keys(cabeceras).sort();
  const canonicas = nombres.map((n) => `${n}:${cabeceras[n]}\n`).join("");
  const firmadas = nombres.join(";");

  const pedidoCanonico = ["POST", "/", "", canonicas, firmadas, sha256(cuerpo)].join("\n");
  const alcance = `${dia}/${REGION}/ec2/aws4_request`;
  const aFirmar = ["AWS4-HMAC-SHA256", momento, alcance, sha256(pedidoCanonico)].join("\n");

  let clave = hmac(`AWS4${process.env.AWS_SECRET_ACCESS_KEY}`, dia);
  for (const parte of [REGION, "ec2", "aws4_request"]) clave = hmac(clave, parte);
  const firma = createHmac("sha256", clave).update(aFirmar, "utf8").digest("hex");

  const respuesta = await fetch(`https://${host}/`, {
    method: "POST",
    headers: {
      ...cabeceras,
      authorization: `AWS4-HMAC-SHA256 Credential=${process.env.AWS_ACCESS_KEY_ID}/`
        + `${alcance}, SignedHeaders=${firmadas}, Signature=${firma}`,
    },
    body: cuerpo,
  });
  const texto = await respuesta.text();
  if (!respuesta.ok) {
    throw new Error(`EC2 ${accion} devolvió ${respuesta.status}: ${texto.slice(0, 400)}`);
  }
  return texto;
}

export async function estadoInstancia() {
  const xml = await ec2("DescribeInstances", { "InstanceId.1": INSTANCIA });
  return {
    estado: /<instanceState>[\s\S]*?<name>([^<]+)<\/name>/.exec(xml)?.[1] || "desconocido",
    // <ipAddress> es la pública; <privateIpAddress> es la otra. Apagada no viene.
    ip: /<ipAddress>([^<]+)<\/ipAddress>/.exec(xml)?.[1] || null,
  };
}

export async function encender() {
  await ec2("StartInstances", { "InstanceId.1": INSTANCIA });
}

// ----------------------------------------------------------- autenticación

/**
 * Valida la basic auth contra un PBKDF2 guardado en variables de entorno.
 *
 * El usuario va adentro del hash junto con la contraseña, y no comparado
 * aparte, para que la comparación entera sea de tiempo constante: si el
 * usuario se chequeara con `===`, el tiempo de respuesta diría si acertaste
 * el nombre.
 */
/**
 * Las cabeceras ya validadas, mientras el contenedor siga caliente.
 *
 * PBKDF2 con 200.000 vueltas tarda unas décimas en el tamaño de Lambda más
 * chico. Cargar la página del asistente son ~20 pedidos (el HTML, el bundle,
 * la hoja de estilos, los recortes), y pagar esas décimas en cada uno suma
 * varios segundos de latencia por visita. Se calcula una vez por contenedor.
 *
 * Vive solo en memoria del contenedor, así que si cambiás la contraseña los
 * que estén calientes siguen aceptando la vieja unos minutos, hasta que
 * Lambda los recicle.
 */
const yaValidadas = new Set();

/** ¿Este `usuario:contraseña` en base64 es el bueno? */
export function credencialValida(base64 = "") {
  if (!SAL || !HASH || !base64) return false;

  const huella = createHash("sha256").update(base64).digest("hex");
  if (yaValidadas.has(huella)) return true;

  let credenciales;
  try {
    credenciales = Buffer.from(base64, "base64").toString("utf8");
  } catch {
    return false;
  }
  if (!credenciales.includes(":")) return false;

  const calculado = pbkdf2Sync(credenciales, SAL, VUELTAS, 32, "sha256");
  const esperado = Buffer.from(HASH, "hex");
  const vale = calculado.length === esperado.length && timingSafeEqual(calculado, esperado);
  // Solo los aciertos. Guardar los fallos dejaría que un bot que insiste
  // deje de pagar el costo de PBKDF2, que es lo que lo hace caro.
  if (vale) yaValidadas.add(huella);
  return vale;
}

/**
 * La credencial del pedido, venga del navegador o de curl.
 *
 * Son dos caminos porque el navegador no puede usar el de curl: las Function
 * URL renombran `WWW-Authenticate` a `x-amzn-Remapped-www-authenticate`, así
 * que el navegador nunca recibe el desafío y jamás muestra el cuadro de
 * usuario y contraseña. Comprobado contra la URL ya desplegada. Por eso el
 * navegador entra por un formulario y queda con una cookie, y la cabecera
 * `Authorization` se sigue aceptando para `curl -u` y para las pruebas.
 *
 * La cookie guarda el mismo `usuario:contraseña` en base64 que guardaría el
 * navegador con basic auth, pero marcada HttpOnly y Secure: no la puede leer
 * el JavaScript de la página ni viaja por HTTP. Se guarda la credencial y no
 * un token firmado porque igual hay que reenviarla a nginx, que la valida con
 * su propio .htpasswd; con un token habría que tener la contraseña guardada
 * en la configuración de la función.
 */
export function credencialDe(event) {
  const cabeceras = event.headers || {};
  const cabecera = cabeceras.authorization || cabeceras.Authorization || "";
  if (cabecera.startsWith("Basic ")) return cabecera.slice(6);

  for (const galleta of event.cookies || []) {
    const corte = galleta.indexOf("=");
    if (corte > 0 && galleta.slice(0, corte).trim() === COOKIE) {
      return galleta.slice(corte + 1).trim();
    }
  }
  return "";
}

// -------------------------------------------------------------- las páginas

const ESTILO = `
  :root{color-scheme:dark}
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    background:radial-gradient(1200px 500px at 15% -8%,#1a233055,transparent 60%),#0a0e13;
    color:#e6edf3;font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  .caja{max-width:30rem;padding:2rem;text-align:center}
  h1{font-size:1.15rem;font-weight:650;margin:0 0 .6rem}
  p{color:#8b98a5;margin:.4rem 0}
  .punto{color:#f59e0b}
  .barra{height:3px;background:#262f3b;border-radius:3px;overflow:hidden;margin:1.4rem 0 .8rem}
  .barra i{display:block;height:100%;width:38%;background:#f59e0b;border-radius:3px;
    animation:v 1.4s ease-in-out infinite}
  @keyframes v{0%{margin-left:-38%}100%{margin-left:100%}}
  small{color:#5b6b7a;font-size:.78rem}
`;

// Nada de lo que se muestra viene del visitante, pero sí puede venir de un
// mensaje de error de AWS: sin escapar, un '<' en ese texto rompe la página.
const escapar = (t) => String(t).replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const paginaArranque = (detalle) => `<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="${ESPERA_SEG}">
<title>Arrancando…</title><style>${ESTILO}</style></head><body><div class="caja">
<h1>Encendiendo el asistente<span class="punto">.</span></h1>
<div class="barra"><i></i></div>
<p>${escapar(detalle)}</p>
<p><small>El servidor está apagado cuando nadie lo usa. Esta página se
actualiza sola; no hace falta hacer nada.</small></p>
</div></body></html>`;

const paginaEntrar = (error = "") => `<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Acceso restringido</title><style>${ESTILO}
  form{display:grid;gap:.6rem;margin-top:1.3rem;text-align:left}
  label{font-size:.78rem;color:#8b98a5}
  input{width:100%;background:#121821;border:1px solid #262f3b;border-radius:8px;
    padding:.55rem .7rem;color:#e6edf3;font:inherit;outline:none}
  input:focus{border-color:#323d4d}
  button{margin-top:.4rem;background:#f59e0b;color:#0a0e13;border:0;border-radius:8px;
    padding:.55rem 1rem;font:inherit;font-weight:650;cursor:pointer}
  button:hover{filter:brightness(1.1)}
  .mal{color:#f87171;font-size:.82rem}
</style></head><body><div class="caja">
<h1>Asistente técnico<span class="punto">.</span></h1>
<p>Acceso restringido.</p>
${error ? `<p class="mal">${escapar(error)}</p>` : ""}
<form method="post" action="${RUTA_ENTRAR}">
  <div><label for="u">Usuario</label><input id="u" name="usuario" autocomplete="username"
       autocapitalize="none" autofocus></div>
  <div><label for="c">Contraseña</label><input id="c" name="clave" type="password"
       autocomplete="current-password"></div>
  <button type="submit">Entrar</button>
</form>
<p><small>El servidor está apagado cuando nadie lo usa: la primera visita
tarda alrededor de un minuto en encenderlo.</small></p>
</div></body></html>`;

function responder(stream, statusCode, headers, cuerpo) {
  const salida = awslambda.HttpResponseStream.from(stream, { statusCode, headers });
  salida.write(cuerpo);
  salida.end();
}

const sinCache = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
};

// -------------------------------------------------------------------- proxy

/**
 * Reenvía el pedido a nginx y vuelca la respuesta en el stream de salida.
 *
 * Se exporta para poder probarlo contra un servidor local: el tramo que
 * importa verificar es que un archivo grande pase entero, porque es
 * justamente donde una Lambda sin streaming se rompe.
 */
export function proxiar(ip, event, stream, puerto = PUERTO, credencial = "") {
  return new Promise((resolver, rechazar) => {
    const metodo = event.requestContext?.http?.method || "GET";
    const ruta = event.rawPath + (event.rawQueryString ? `?${event.rawQueryString}` : "");

    const cabeceras = {};
    for (const [nombre, valor] of Object.entries(event.headers || {})) {
      const n = nombre.toLowerCase();
      // `host` se reemplaza por el destino y `content-length` lo recalcula
      // Node; reenviar el original descoloca el cuerpo si venía en base64.
      if (POR_CONEXION.has(n) || n === "host" || n === "content-length") continue;
      cabeceras[n] = valor;
    }
    cabeceras.host = ip;
    // nginx valida la misma clave con su .htpasswd. Se la arma acá porque el
    // navegador la trae en una cookie, no en la cabecera: así al visitante se
    // le pide una sola vez y nginx sigue protegido aunque alguien dé con la
    // IP de la instancia.
    if (credencial) cabeceras.authorization = `Basic ${credencial}`;

    const cuerpo = event.body
      ? Buffer.from(event.body, event.isBase64Encoded ? "base64" : "utf8")
      : null;
    if (cuerpo) cabeceras["content-length"] = String(cuerpo.length);

    const pedido = http.request(
      { host: ip, port: puerto, method: metodo, path: ruta, headers: cabeceras },
      (respuesta) => {
        const limpias = {};
        for (const [nombre, valor] of Object.entries(respuesta.headers)) {
          if (POR_CONEXION.has(nombre)) continue;
          limpias[nombre] = Array.isArray(valor) ? valor.join(", ") : valor;
        }
        const salida = awslambda.HttpResponseStream.from(stream, {
          statusCode: respuesta.statusCode,
          headers: limpias,
        });
        respuesta.pipe(salida);
        respuesta.on("end", resolver);
        respuesta.on("error", (e) => {
          // Ya se mandaron las cabeceras: quien llame no puede devolver otra
          // página, solo registrar que la respuesta salió cortada.
          e.yaRespondio = true;
          rechazar(e);
        });
      });

    // 25 s: una consulta tarda 2 s en caliente y 6 s con la API recién
    // levantada. Si no contestó en 25 es que algo se colgó, y conviene
    // devolver la página de arranque antes que dejar la pestaña esperando.
    pedido.setTimeout(25000, () => pedido.destroy(new Error("nginx no contestó en 25 s")));
    pedido.on("error", rechazar);
    if (cuerpo) pedido.write(cuerpo);
    pedido.end();
  });
}

// ------------------------------------------------------------------ handler

export async function manejar(event, stream) {
  const metodo = event.requestContext?.http?.method || "GET";
  const credencial = credencialDe(event);

  // El formulario. Va antes que todo lo demás: es el único pedido que puede
  // llegar sin credencial y no ser un bot.
  if (event.rawPath === RUTA_ENTRAR) {
    if (metodo !== "POST") return responder(stream, 200, sinCache, paginaEntrar());

    const cuerpo = new URLSearchParams(
      Buffer.from(event.body || "", event.isBase64Encoded ? "base64" : "utf8").toString("utf8"));
    const propuesta = Buffer.from(
      `${cuerpo.get("usuario") || ""}:${cuerpo.get("clave") || ""}`).toString("base64");

    if (!credencialValida(propuesta)) {
      return responder(stream, 401, sinCache, paginaEntrar("Usuario o contraseña incorrectos."));
    }
    return responder(stream, 303, {
      ...sinCache,
      location: "/",
      "set-cookie": `${COOKIE}=${propuesta}; Path=/; HttpOnly; Secure; `
        + `SameSite=Lax; Max-Age=${COOKIE_SEG}`,
    }, "");
  }

  if (!credencialValida(credencial)) {
    // 401 con el formulario, y sin `WWW-Authenticate`: la Function URL la
    // renombra a `x-amzn-Remapped-www-authenticate`, así que el navegador no
    // la ve y mandarla solo confundiría a quien lea las cabeceras.
    return responder(stream, 401, sinCache, paginaEntrar());
  }

  let instancia;
  try {
    instancia = await estadoInstancia();
  } catch (e) {
    // El detalle va a CloudWatch y no a la página: el error de AWS trae su
    // id de request y su texto, que al visitante no le dicen nada.
    console.error("DescribeInstances falló:", e.message);
    return responder(stream, 502, sinCache,
      paginaArranque("No se pudo consultar el estado del servidor. "
        + "Reintentando; si sigue, revisá los logs de la función."));
  }

  if (instancia.estado === "stopped") {
    try {
      await encender();
    } catch (e) {
      // `stopping` todavía en curso devuelve IncorrectInstanceState: no es un
      // error que valga mostrar, el refresh siguiente lo reintenta.
      return responder(stream, 503, sinCache,
        paginaArranque("El servidor se estaba terminando de apagar. Reintentando…"));
    }
    return responder(stream, 503, sinCache,
      paginaArranque("Tarda alrededor de un minuto la primera vez."));
  }

  if (instancia.estado !== "running" || !instancia.ip) {
    return responder(stream, 503, sinCache,
      paginaArranque(`Estado del servidor: ${instancia.estado}.`));
  }

  try {
    await proxiar(instancia.ip, event, stream, PUERTO, credencial);
  } catch (e) {
    if (e.yaRespondio) {
      // La respuesta se cortó a mitad y las cabeceras ya salieron: escribir
      // la página de arranque acá produciría un cuerpo mezclado con lo que
      // ya se había enviado.
      console.error("respuesta cortada a mitad:", e.message);
      return stream.end();
    }
    // Encendida pero todavía sin nginx: los contenedores tardan ~24 s más.
    return responder(stream, 503, sinCache,
      paginaArranque("Ya encendió; están levantando los servicios."));
  }
}

export const handler = globalThis.awslambda
  ? globalThis.awslambda.streamifyResponse(manejar)
  : manejar;
