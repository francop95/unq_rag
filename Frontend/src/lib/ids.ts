/**
 * Identificadores para conversaciones y mensajes.
 *
 * No se usa `crypto.randomUUID()` directamente porque **solo existe en
 * contextos seguros**: HTTPS o localhost. Servida por HTTP sobre una IP —que es
 * el caso del despliegue de prueba en EC2— la función es `undefined` y la app
 * se caía al arrancar con "crypto.randomUUID is not a function", sin renderizar
 * nada. En local nunca se veía, porque localhost sí cuenta como seguro.
 *
 * `crypto.getRandomValues()` sí está disponible en contextos inseguros, así que
 * el fallback sigue siendo aleatoriedad criptográfica y no un Math.random.
 */

/** UUID v4 a partir de bytes aleatorios. */
function uuidFromBytes(bytes: Uint8Array): string {
  // Versión (4) y variante (10xx), según RFC 4122.
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;

  const hex: string[] = [];
  for (let i = 0; i < 16; i++) {
    hex.push(bytes[i].toString(16).padStart(2, "0"));
  }
  return (
    hex.slice(0, 4).join("") +
    "-" +
    hex.slice(4, 6).join("") +
    "-" +
    hex.slice(6, 8).join("") +
    "-" +
    hex.slice(8, 10).join("") +
    "-" +
    hex.slice(10, 16).join("")
  );
}

export function newId(): string {
  const c = globalThis.crypto as Crypto | undefined;

  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }

  if (c && typeof c.getRandomValues === "function") {
    return uuidFromBytes(c.getRandomValues(new Uint8Array(16)));
  }

  // Sin Web Crypto no hay navegador que nos importe, pero estos ids solo tienen
  // que ser únicos —no secretos—, así que vale terminar con algo que funcione.
  const bytes = new Uint8Array(16);
  for (let i = 0; i < 16; i++) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  return uuidFromBytes(bytes);
}
