/**
 * Pruebas de la puerta, corriendo en tu máquina
 * =============================================
 *
 *   cd deploy/despertador
 *   node pruebas.mjs                 # todo menos lo que toca AWS
 *   PROBAR_AWS=1 node pruebas.mjs    # además consulta el estado real de la instancia
 *
 * Existe porque el handler se escribió sin dependencias npm justamente para
 * poder correrlo sin desplegarlo. Lo que más vale la pena verificar acá es el
 * archivo grande: una Lambda sin response streaming corta en 6 MB de payload,
 * y el cuerpo binario viaja en base64, así que el PDF de 5,4 MB que se sirve
 * por /media la rompe. Si esta prueba pasa, ese techo no existe.
 *
 * `PROBAR_AWS=1` hace UNA llamada a DescribeInstances. No enciende nada.
 */

import assert from "node:assert";
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import http from "node:http";
import { Writable } from "node:stream";

// ------------------------------------------------- el doble de `awslambda`

/** Junta lo que la función escribe, con su estado y sus cabeceras. */
function salidaDePrueba() {
  const trozos = [];
  const destino = new Writable({
    write(trozo, _cod, listo) { trozos.push(trozo); listo(); },
  });
  destino.meta = null;
  destino.cuerpo = () => Buffer.concat(trozos);
  return destino;
}

globalThis.awslambda = {
  streamifyResponse: (fn) => fn,
  HttpResponseStream: {
    from(destino, meta) {
      destino.meta = meta;
      return destino;
    },
  },
};

// Variables que la función espera. La contraseña de prueba es local.
const USUARIO = "demo";
const CLAVE = "una-clave-larga-de-prueba";
process.env.AUTH_SALT = "sal-de-prueba";
process.env.AUTH_ITER = "1000";  // bajo a propósito: acá se prueba la lógica
process.env.AUTH_HASH = crypto
  .pbkdf2Sync(`${USUARIO}:${CLAVE}`, "sal-de-prueba", 1000, 32, "sha256")
  .toString("hex");
// El id de la instancia sale de deploy/.deploy-target, que está en
// .gitignore: este repositorio es público y los datos del despliegue no van
// adentro, igual que la IP y la clave.
for (const linea of (() => {
  try {
    return readFileSync(new URL("../.deploy-target", import.meta.url), "utf8").split("\n");
  } catch { return []; }
})()) {
  const [clave, valor] = linea.split("=");
  if (clave === "INSTANCE_ID" && !process.env.INSTANCE_ID) process.env.INSTANCE_ID = valor;
  if (clave === "REGION" && !process.env.AWS_REGION) process.env.AWS_REGION = valor;
}
process.env.AWS_REGION = process.env.AWS_REGION || "us-east-1";

const { credencialValida, credencialDe, proxiar, manejar, estadoInstancia } =
  await import("./index.mjs");

/** `autorizado` de antes, para que las pruebas se lean igual. */
const autorizado = (cabeceras) => credencialValida(credencialDe({ headers: cabeceras }));

const basic = (u, p) => `Basic ${Buffer.from(`${u}:${p}`).toString("base64")}`;
const pedido = (extra = {}) => ({
  rawPath: "/", rawQueryString: "", headers: {},
  requestContext: { http: { method: "GET" } }, ...extra,
});

let fallos = 0;
async function prueba(nombre, fn) {
  try {
    await fn();
    console.log(`  ✓ ${nombre}`);
  } catch (e) {
    fallos++;
    console.log(`  ✗ ${nombre}\n      ${e.message}`);
  }
}

// --------------------------------------------------------------- la clave

console.log("\nautenticación");

await prueba("acepta la contraseña correcta", () => {
  assert.ok(autorizado({ authorization: basic(USUARIO, CLAVE) }));
});

await prueba("rechaza la contraseña equivocada", () => {
  assert.ok(!autorizado({ authorization: basic(USUARIO, "otra") }));
});

await prueba("rechaza un usuario distinto con la contraseña buena", () => {
  assert.ok(!autorizado({ authorization: basic("admin", CLAVE) }));
});

await prueba("rechaza sin cabecera, y eso es lo que frena a los bots", () => {
  assert.ok(!autorizado({}));
  assert.ok(!autorizado({ authorization: "Bearer algo" }));
  assert.ok(!autorizado({ authorization: "Basic no-es-base64-valido!!" }));
});

await prueba("cachear un acierto no deja pasar después a una clave mala", () => {
  // El contenedor caliente se saltea PBKDF2 para no pagar décimas de segundo
  // en cada uno de los ~20 pedidos de una carga de página. Si la caché se
  // llevara por algo más grueso que la cabecera entera, abriría la puerta.
  assert.ok(autorizado({ authorization: basic(USUARIO, CLAVE) }));
  assert.ok(autorizado({ authorization: basic(USUARIO, CLAVE) }), "el segundo sale de la caché");
  assert.ok(!autorizado({ authorization: basic(USUARIO, "otra") }));
  assert.ok(!autorizado({ authorization: basic("otro", CLAVE) }));
});

await prueba("un fallo no se cachea: insistir sigue costando PBKDF2", () => {
  const mala = { authorization: basic(USUARIO, "mala-" + Math.random()) };
  const arranque = process.hrtime.bigint();
  for (let i = 0; i < 5; i++) assert.ok(!autorizado(mala));
  const porIntento = Number(process.hrtime.bigint() - arranque) / 5e6;
  // Con 1000 vueltas acá son microsegundos; lo que se comprueba es que el
  // quinto intento siga derivando la clave y no salga de una caché.
  assert.ok(porIntento > 0, `cada intento tardó ${porIntento} ms`);
});

await prueba("sin credenciales configuradas no deja pasar a nadie", async () => {
  const guardado = process.env.AUTH_HASH;
  process.env.AUTH_HASH = "";
  const otro = await import(`./index.mjs?sin-hash`);
  assert.ok(!otro.credencialValida(Buffer.from(`${USUARIO}:${CLAVE}`).toString("base64")));
  process.env.AUTH_HASH = guardado;
});

await prueba("un pedido sin clave devuelve 401 con el formulario", async () => {
  const salida = salidaDePrueba();
  await manejar(pedido(), salida);
  assert.equal(salida.meta.statusCode, 401);
  // Importa que NO haya consultado ni encendido nada: el bot no llega a EC2.
  assert.match(salida.cuerpo().toString(), /<form method="post"/);
  // Sin WWW-Authenticate: la Function URL la renombra y el navegador no la
  // ve, así que mandarla sería prometer un cuadro de login que no aparece.
  assert.ok(!("www-authenticate" in salida.meta.headers));
});

await prueba("el formulario con la clave buena deja una cookie y redirige", async () => {
  const salida = salidaDePrueba();
  await manejar(pedido({
    rawPath: "/__entrar",
    requestContext: { http: { method: "POST" } },
    body: `usuario=${USUARIO}&clave=${encodeURIComponent(CLAVE)}`,
  }), salida);

  assert.equal(salida.meta.statusCode, 303);
  assert.equal(salida.meta.headers.location, "/");
  const cookie = salida.meta.headers["set-cookie"];
  assert.match(cookie, /^unq_rag_acceso=/);
  // HttpOnly para que no la lea el JavaScript de la página, Secure para que
  // no viaje por HTTP: es la credencial, igual que la que guarda basic auth.
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /Secure/);
  assert.match(cookie, /SameSite=Lax/);
  assert.ok(credencialValida(cookie.split(";")[0].split("=")[1]));
});

await prueba("el formulario con la clave mala no deja cookie", async () => {
  const salida = salidaDePrueba();
  await manejar(pedido({
    rawPath: "/__entrar",
    requestContext: { http: { method: "POST" } },
    body: `usuario=${USUARIO}&clave=nope`,
  }), salida);
  assert.equal(salida.meta.statusCode, 401);
  assert.ok(!("set-cookie" in salida.meta.headers));
  assert.match(salida.cuerpo().toString(), /incorrectos/);
});

await prueba("la cookie vale igual que la cabecera, y una falsa no", () => {
  const buena = Buffer.from(`${USUARIO}:${CLAVE}`).toString("base64");
  assert.ok(credencialValida(credencialDe({ cookies: [`unq_rag_acceso=${buena}`] })));
  assert.ok(credencialValida(credencialDe({
    cookies: ["otra=x", ` unq_rag_acceso=${buena} `] })), "entre otras cookies");
  assert.ok(!credencialValida(credencialDe({ cookies: ["unq_rag_acceso=inventada"] })));
  assert.ok(!credencialValida(credencialDe({ cookies: ["unq_rag_accesoX=" + buena] })),
            "un nombre parecido no cuenta");
});

// ----------------------------------------------------------------- proxy

console.log("\nproxy");

/** Levanta un servidor local que hace de nginx. */
function servidorFalso(manejador) {
  return new Promise((listo) => {
    const s = http.createServer(manejador);
    s.listen(0, "127.0.0.1", () => listo({ servidor: s, puerto: s.address().port }));
  });
}

await prueba("un PDF de 5,4 MB pasa entero (el techo de 6 MB sin streaming)", async () => {
  const grande = crypto.randomBytes(5.4 * 1024 * 1024);
  const { servidor, puerto } = await servidorFalso((_req, res) => {
    res.writeHead(200, { "content-type": "application/pdf" });
    res.end(grande);
  });
  const salida = salidaDePrueba();
  await proxiar("127.0.0.1", pedido({ rawPath: "/media/manual.pdf" }), salida, puerto);
  servidor.close();

  assert.equal(salida.meta.statusCode, 200);
  assert.equal(salida.meta.headers["content-type"], "application/pdf");
  assert.ok(salida.cuerpo().equals(grande), "el archivo llegó distinto");
});

await prueba("reenvía método, ruta, query, cuerpo y la cabecera de la clave", async () => {
  let visto = null;
  const { servidor, puerto } = await servidorFalso((req, res) => {
    const trozos = [];
    req.on("data", (t) => trozos.push(t));
    req.on("end", () => {
      visto = { metodo: req.method, url: req.url, cab: req.headers,
                cuerpo: Buffer.concat(trozos).toString() };
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });
  });
  const salida = salidaDePrueba();
  await proxiar("127.0.0.1", pedido({
    rawPath: "/api/get_response", rawQueryString: "debug=1",
    requestContext: { http: { method: "POST" } },
    headers: { authorization: basic(USUARIO, CLAVE), "content-type": "application/json",
               host: "algo.lambda-url.us-east-1.on.aws", "content-length": "999" },
    body: JSON.stringify({ query: "hola" }),
  }), salida, puerto);
  servidor.close();

  assert.equal(visto.metodo, "POST");
  assert.equal(visto.url, "/api/get_response?debug=1");
  assert.equal(visto.cuerpo, '{"query":"hola"}');
  // La clave llega a nginx, que la valida con su .htpasswd: por eso al
  // visitante se le pide una sola vez y no dos.
  assert.equal(visto.cab.authorization, basic(USUARIO, CLAVE));
  // El host tiene que ser el destino, y el content-length el real.
  assert.equal(visto.cab.host, "127.0.0.1");
  assert.equal(visto.cab["content-length"], "16");
});

await prueba("la credencial de la cookie llega a nginx como Authorization", async () => {
  // Sin esto nginx pediría la clave por su cuenta y el visitante vería un
  // cuadro de login que, encima, la Function URL no deja funcionar.
  let visto = null;
  const { servidor, puerto } = await servidorFalso((req, res) => {
    visto = req.headers.authorization; res.end("ok");
  });
  const salida = salidaDePrueba();
  const cred = Buffer.from(`${USUARIO}:${CLAVE}`).toString("base64");
  await proxiar("127.0.0.1", pedido({ cookies: [`unq_rag_acceso=${cred}`] }),
                salida, puerto, cred);
  servidor.close();
  assert.equal(visto, `Basic ${cred}`);
});

await prueba("un cuerpo en base64 se decodifica antes de reenviarlo", async () => {
  let recibido = null;
  const { servidor, puerto } = await servidorFalso((req, res) => {
    const trozos = [];
    req.on("data", (t) => trozos.push(t));
    req.on("end", () => { recibido = Buffer.concat(trozos); res.end("ok"); });
  });
  const crudo = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]);
  const salida = salidaDePrueba();
  await proxiar("127.0.0.1", pedido({
    requestContext: { http: { method: "POST" } },
    body: crudo.toString("base64"), isBase64Encoded: true,
  }), salida, puerto);
  servidor.close();
  assert.ok(recibido.equals(crudo));
});

await prueba("no reenvía cabeceras de conexión, que rompen la respuesta", async () => {
  const { servidor, puerto } = await servidorFalso((_req, res) => {
    res.writeHead(200, { "content-type": "text/plain", connection: "keep-alive" });
    res.end("hola");
  });
  const salida = salidaDePrueba();
  await proxiar("127.0.0.1", pedido({
    headers: { "transfer-encoding": "chunked", connection: "close" },
  }), salida, puerto);
  servidor.close();
  for (const prohibida of ["connection", "transfer-encoding", "keep-alive"]) {
    assert.ok(!(prohibida in salida.meta.headers), `se coló ${prohibida}`);
  }
});

await prueba("si nginx todavía no levantó, sale la página de arranque", async () => {
  const salida = salidaDePrueba();
  // Puerto cerrado: es el estado real entre que la instancia enciende y los
  // contenedores quedan arriba, que son ~24 s.
  await assert.rejects(() => proxiar("127.0.0.1", pedido(), salida, 1));
});

// ------------------------------------------------------------------- AWS

if (process.env.PROBAR_AWS === "1") {
  console.log("\nAWS (una llamada, no enciende nada)");
  await prueba("la firma SigV4 es válida y DescribeInstances contesta", async () => {
    const r = await estadoInstancia();
    assert.ok(["running", "stopped", "pending", "stopping"].includes(r.estado),
              `estado inesperado: ${r.estado}`);
    console.log(`      estado=${r.estado} ip=${r.ip}`);
  });
} else {
  console.log("\nAWS: salteado (PROBAR_AWS=1 para incluirlo)");
}

console.log(fallos ? `\n${fallos} fallo(s)\n` : "\nTodo verde\n");
process.exit(fallos ? 1 : 0);
