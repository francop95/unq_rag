# La pantalla de revisión de la ingesta

Para operar la ingesta sin haberla construido: levantar la pantalla, meter un
documento nuevo al índice, y entender qué hace cada botón antes de apretarlo.

## Por qué existe

El troceo de un documento lo hace un modelo de visión, y se equivoca de formas
que ningún reporte automático detecta. Los cuatro reportes que genera el
pipeline (`quality_report.json`, `validation_report.json`, y los otros dos)
miden **estructura**, no verdad: la chapa de un motor pasó los cuatro como
válida diciendo "Conexión A/Y" cuando el plano dice Δ/Y.

Por eso hay una compuerta entre trocear e indexar. El troceo es lo caro y ya
quedó en disco; de ahí para adelante se gastan embeddings y se escribe el
índice, así que es el punto donde tiene sentido que alguien mire lo que salió.
Esta pantalla es esa compuerta.

Lo que se revisa son **las figuras y las tablas**, no los miles de chunks de
texto. El texto plano sale bien; lo que se lee mal es un plano a 144 dpi.

## Levantarla

```bash
cd Ingestion
./.venv/bin/python scripts/revisar_web.py
```

Queda en **http://127.0.0.1:5055**. Con `--puerto 5060` si ese está ocupado.

Escucha solo en `127.0.0.1` **a propósito**: desde acá se lanza el troceo y la
relectura de figuras, que gastan plata, y no pide contraseña. Si la exponés con
`--host`, te lo avisa por consola.

Flask corre sin modo debug, así que **no recarga sola**: si tocás el código,
hay que reiniciarla.

Lo mismo en consola, si preferís: `scripts/revisar_ingesta.py`. Las dos leen
las mismas corridas y escriben la misma base, así que se pueden mezclar.

## Indexar un documento nuevo, de punta a punta

1. **Dejá el PDF en `data/raw_data/`.** De ahí sale el listado de la izquierda.
   Si no aparece, revisá la extensión.

2. **Apretá "Volver a trocear"** (el documento todavía no tiene corridas, así
   que esto es su primer troceo). El modelo de visión lee cada página y escribe
   los chunks en `data/chunks_data/<documento>/<timestamp>/`. Ese timestamp es
   la **corrida**.

   Si el documento tiene planos, acá adentro corre además una segunda lectura a
   alta resolución de cada plano (ver *La relectura de planos*, más abajo). Son
   varios minutos.

   El pipeline **se frena** después de trocear. No indexa nada.

3. **Revisá las figuras y tablas.** Cada una tiene su recorte a la vista y
   cuatro botones. Mientras quede alguna marcada `redescribir` o `editar`, el
   documento no se puede aprobar.

4. **Revisá las conexiones**, si es un plano. Pestaña "Conexiones del plano".

5. **Aprobar.** Recién ahí la corrida queda habilitada para indexar.

6. **Indexar.** Reutiliza los chunks que ya están en disco —no vuelve a llamar
   al modelo de visión— y escribe embeddings e índice.

7. **Reconstruir el grafo**, si confirmaste conexiones. Sin esto, el grafo le
   sigue afirmando al modelo de respuesta lo que la visión leyó mal.

8. **Subir a la demo**, si querés que se vea desde afuera.

Los pasos 2, 6 y 7 lanzan procesos largos. **Corre uno a la vez**: si hay un
trabajo en curso, los botones devuelven "ya hay algo corriendo". El panel de
abajo muestra el log en vivo y tiene un botón para cancelar.

## Los botones de la cabecera

### Decidir sobre la corrida

| Botón | Qué hace |
|---|---|
| **Aprobar** | Marca la corrida como buena y habilita indexarla. **Se niega** si quedó trabajo marcado sin hacer, y te dice cuáles: aprobar con una figura marcada `redescribir` sería dar por bueno lo que vos mismo marcaste como malo, y el chunk se indexaría igual con el veredicto al lado. |
| **Rechazar** | La corrida no se indexa hasta volver a trocearla. Pide confirmación. |

### Llevarlo al índice

| Botón | Qué hace | Cuesta |
|---|---|---|
| **Indexar** | Corre el pipeline con `--reusar-chunks --exigir-revision`. Si la corrida no está aprobada, se frena en la compuerta y **no toca nada** — ni embeddings, ni índice, ni media. | embeddings |
| **Reconstruir el grafo** | Dos pasos en orden: reescribe el grafo desde el inventario más lo que confirmaste acá, y después le agrega lo que extrae de los textos con el modelo. | unos centavos |
| **Subir a la demo** | Sube código e índice al servidor público. Si está apagado se enciende solo. Tarda unos minutos y lo ve quien entre con la contraseña. | — |

Ojo con "Indexar": si la corrida no está aprobada, **el trabajo termina en
"terminado", no en error** — saltear un documento no es un fallo del pipeline.
El motivo está en el log:

```
🛑 --exigir-revision: la corrida 20260925_114016 está 'pendiente'. No se indexa.
```

### Para revisar

| Botón | Qué hace |
|---|---|
| **Comparar con la aprobada** | Diff contra la última corrida aprobada del mismo documento: qué chunks son nuevos, cuáles desaparecieron, cuáles cambiaron. Sirve para ver si volver a trocear mejoró o empeoró. |
| **Conexiones del plano** | Abre y cierra la tabla de aristas. |
| **Ver también el texto** | Por defecto la lista muestra solo figuras y tablas; esto agrega los chunks de texto plano. |

### Rehacer

**Volver a trocear** vuelve a pasar el documento por el modelo de visión y crea
una corrida nueva, con otro timestamp. No toca el índice. Va siempre con
`--forzar`: la ingesta por lotes saltea un documento que no cambió, y ahí eso
está bien, pero acá vos elegiste ese documento y apretaste el botón, así que
saltearlo sería un trabajo que termina "bien" sin haber hecho nada.

Las corridas viejas **no se borran**. Podés volver a cualquiera con el
desplegable de arriba.

## Las fichas de arriba

```
10 chunks    2 figuras y tablas    0 con veredicto    2 figuras sin mirar
```

- **chunks** — todo lo que salió del documento, texto incluido.
- **figuras y tablas** — lo único que necesita ojo humano.
- **con veredicto** — cuántas de esas ya decidiste.
- **figuras sin mirar** — las que faltan. Esta es la que importa.

## Los botones de cada figura

| Botón | Veredicto | Qué significa |
|---|---|---|
| **Entra así** | `ok` | Entra al índice tal como está. |
| **Releer** | `corregido` | Vuelve a leer **esa** figura con el modelo de visión. Son varias llamadas: se transcribe dos veces con encuadres distintos y recién después se describe. **Cuesta plata.** |
| **Editar** | `corregido` | Reescribís el texto a mano. Lo que guardes gana sobre lo que leyó el modelo, en todos lados: el índice, el enriquecimiento y los validadores. |
| **Descartar** | `descartar` | Ese chunk no se indexa. |

Hay dos veredictos más que no son botones, y son los que frenan: `redescribir`
y `editar` se ponen como **trabajo pendiente**. `corregido` es ese mismo trabajo
ya hecho, así que no frena nada.

Cambiar un veredicto sobre una corrida ya aprobada **la devuelve a pendiente**.
Es a propósito: si no, la aprobación diría algo que ya no es cierto.

## La pestaña de conexiones

Una tabla de aristas: `QD01 → QF03`, con su evidencia y de qué página salió.

**Se llena sola al abrirla** si está vacía, con lo que el troceo ya había leído
de la figura. Sin eso, la pestaña arrancaba vacía aunque el chunk viniera
afirmando dieciséis conexiones: dos lecturas del mismo dibujo, y la pantalla
mostraba una en el texto y ninguna en la tabla.

Abajo hay botones para agregar una segunda tanda, de una pasada de modelo que
pregunta componente por componente ("¿a quién alimenta QF03?"): **`p.1`**,
**`p.2`** para una página suelta, y **"Leer las N"** para todas de una si hay
más de una. **Cuesta plata**, y por eso solo ofrece las figuras que tienen
topología que leer — una foto no tiene conexiones, y una pared de botones sobre
figuras que no son planos tiraría plata sobre una foto.

Cada fila tiene tres acciones:

| Acción | Estado | Qué pasa |
|---|---|---|
| **Es así** | `confirmada` | Entra al grafo con la misma autoridad que el inventario. |
| **Corregir** | — | Cambiás origen o destino. El código se elige de un desplegable con **todo el inventario**, o se escribe a mano si no está. |
| **No existe** | `descartada` | Borra esa arista y **evita que vuelva**: ni el grafo ni la capa de extracción la agregan después. |

Mientras una arista esté en `propuesta`, para el grafo **no existe**. Solo lo
confirmado cuenta.

### Por qué hace falta todo esto

`QF03 → IF01` es falso: la visión confundió una línea de llamada con un
conductor. Llegó al grafo con confianza *"directa"* y con evidencia la cadena
`"QF03 → IF01"` — o sea, validada contra el texto del chunk que venía del mismo
error. El grafo se la afirmaba al modelo de respuesta como leída del plano.

La tabla es el único lugar donde una persona contradice a la máquina y la
contradicción queda.

## Qué significa cada estado

De la corrida, arriba del todo:

| Estado | Qué es |
|---|---|
| **sin registrar** | La carpeta de chunks existe, pero nadie la abrió en esta pantalla. Son corridas viejas, de antes de la compuerta. |
| **pendiente** | Registrada, sin decidir. |
| **aprobada** | Lista para indexar. |
| **rechazada** | No se indexa, decisión explícita. Vale aunque no se pida exigir revisión — si no, aprobar y rechazar darían lo mismo. |

## Dónde queda cada cosa

```
data/raw_data/                          los documentos a ingestar
data/chunks_data/<doc>/<timestamp>/     una carpeta por corrida
    ├── <doc>_1/, <doc>_2/              un JSON por chunk, por página
    ├── crops/                          los recortes de las figuras
    ├── validated_chunks.json           los agregados: esto es lo que se indexa
    ├── chunks_base.json
    ├── chunks_for_embedding.json
    ├── planos_relectura.json           la lectura cruda de los planos
    └── *_report.json                   los cuatro reportes
data/revisiones.sqlite3                 todo lo que decidiste en la pantalla
../API/configs/grafo.json               el grafo que la API le pasa al modelo
```

Las rutas de salida del indexado (embeddings, media, índice) salen del `.env`.

**El sqlite se crea solo.** No hay migración ni setup: cada vez que algo se
conecta corre `CREATE TABLE IF NOT EXISTS`. Si lo borrás, se rehace vacío y
perdés las decisiones, no los documentos. Tres tablas: `corridas` (estado de
cada corrida), `veredictos` (lo decidido por chunk) y `conexiones` (las
aristas). Se puede mover con `REVISION_DB_PATH`.

## La relectura de planos

El troceo rasteriza la página a 144 dpi, y la API de visión además reescala el
lado corto a 768 px: un plano A4 denso le llega al modelo con los rótulos en
unos pocos píxeles, donde "30mA" y "30A" son el mismo manchón. De ahí salió un
"interruptor de 30A" que no existe en el tablero.

Por eso, **durante el troceo**, cada plano se vuelve a leer a 6x partido en
mosaicos. No hay que correr nada a mano: es un paso del pipeline. Medido sobre
el plano codificado, pasa de 32 a 45 componentes en una página y de 11 a 29 en
la otra, y declara entre 6 y 7 cosas que no pudo leer, donde el troceo afirmaba
sin dudar ninguna.

Dos cosas que conviene saber:

- **Los recorridos los sigue poniendo el troceo, no la relectura.** Cada pasada
  ve algo distinto: el troceo mira la hoja entera —borrosa, pero puede seguir un
  conductor de punta a punta y nombra por código— y la relectura mira mosaicos,
  donde el texto se lee pero los conductores se cortan en cada borde. Las
  aristas que ve la relectura quedan guardadas aparte.
- **Corre una vez por corrida.** Trocear e indexar son dos pasadas sobre la
  misma carpeta; la segunda saltea los planos ya releídos en vez de pagarlos de
  nuevo.

## Lo que cuesta plata

Para tenerlo presente antes de apretar:

- **Volver a trocear** — el modelo de visión sobre cada página, más la
  relectura de cada plano.
- **Releer** una figura — varias llamadas por figura.
- **Leer las N figuras** de la pestaña de conexiones — una pasada por
  componente.
- **Indexar** — embeddings de todos los chunks.
- **Reconstruir el grafo** — unos centavos, en el segundo paso.

**Aprobar**, **Rechazar**, los veredictos y las decisiones de conexiones no
cuestan nada: solo escriben en el sqlite.

## Cuando algo no anda

**El puerto está ocupado.** Ya hay una pantalla levantada. `lsof -ti :5055` te
dice cuál.

**El desplegable de códigos sale vacío.** Falta el Excel del inventario en
`data/raw_data/`. La pantalla no se rompe —te deja escribir el código a mano—
pero perdés la lista.

**"Reconstruir el grafo" falla.** Lo primero que abre es ese mismo Excel.

**El documento no aparece en el listado.** Tiene que estar en `data/raw_data/`
y con una extensión soportada.

**Indexé y el asistente no lo encuentra.** Mirá a qué `index_path` apunta el
`.env`: si no es el índice que usa la API, indexaste a otro lado.
