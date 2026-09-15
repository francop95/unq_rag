# Desplegar en AWS para que lo prueben otras personas

Una instancia EC2 `t3.small` corriendo el mismo `docker compose` que en local,
con nginx pidiendo usuario y contraseña.

```
  internet
     │  :80  (único puerto abierto, con basic auth)
     ▼
┌──────────────────────── EC2 t3.small · 2 GB ────────────────────────┐
│  web (nginx)  ──┬──► api           127.0.0.1:5000                   │
│                 └──► api-baseline  127.0.0.1:5001                   │
│                         │                                           │
│                    ~/unq-rag/Ingestion/data   (índice subido)       │
└─────────────────────────────────────────────────────────────────────┘
```

**La ingesta no se despliega.** El índice se sube ya construido, así que el
servidor no necesita la imagen de 2,8 GB, ni torch, ni Tesseract, ni gastar en
OpenAI para reindexar.

---

## Antes de empezar

**1. Instalá y configurá el AWS CLI.**

```bash
brew install awscli
aws configure          # access key, secret, región (ej. us-east-1)
```

Usá un usuario IAM con permisos de EC2, no las claves de root de la cuenta.

**2. Poné un límite de gasto en OpenAI.** En
<https://platform.openai.com/settings/organization/limits>. Es la protección
que de verdad importa: la basic auth evita que un bot te encuentre, pero
cualquiera con la contraseña puede hacer consultas, y cada una cuesta.

**3. Rotá la API key si no lo hiciste.** La que está en tu `.env` quedó impresa
en la terminal hace varios turnos.

**4. Verificá que tenés el `.env` de la raíz** con `OPENAI_API_KEY` (el que usa
docker compose).

---

## Desplegar

```bash
./deploy/provision.sh      # crea la infra y pide la contraseña del sitio
./deploy/upload-data.sh    # sube código + índice, construye y levanta
```

`provision.sh` te pregunta el usuario y la contraseña para el sitio, y deja:

| Archivo | Qué es |
|---|---|
| `deploy/unq-rag-key.pem` | clave SSH privada — **sin esto no entrás a la instancia** |
| `deploy/.htpasswd` | credenciales del sitio (hash apr1) |
| `deploy/.deploy-target` | IP e id de la instancia, lo lee `upload-data.sh` |

Los tres están en `.gitignore`.

Al final, `upload-data.sh` imprime la URL: `http://<ip>`. Eso es lo que
compartís, junto con el usuario y la contraseña.

Los dos scripts son idempotentes: si los volvés a correr, reutilizan lo que ya
existe. `upload-data.sh` es además la forma de **actualizar** el despliegue
después de cambiar código.

---

## Qué crea en tu cuenta

| Recurso | Detalle |
|---|---|
| Instancia EC2 | `t3.small`, Amazon Linux 2023, tag `Name=unq-rag` |
| Disco | 30 GB gp3 (imágenes ~1,1 GB + índice ~230 MB + margen) |
| Security group | `unq-rag-sg`: puerto 80 abierto, SSH solo desde tu IP |
| Par de claves | `unq-rag-key` |

**Coste aproximado:** ~15 USD/mes la instancia + ~2,5 USD/mes el disco, más el
tráfico de salida. Se puede parar cuando no se usa:

```bash
aws ec2 stop-instances  --instance-ids <id>    # deja de cobrar cómputo
aws ec2 start-instances --instance-ids <id>    # la IP pública CAMBIA
```

Si la paras y la arrancás, hay que volver a correr `provision.sh` para que
actualice la IP en `.deploy-target`.

---

## Lo que se sube y lo que no

`upload-data.sh` sube solo lo que la API necesita en runtime (~230 MB):

| Directorio | Por qué |
|---|---|
| `chroma_index/`, `chroma_index_baseline/` | los índices vectoriales |
| `chunks_data/`, `chunks_data_baseline/` | los lee el context expander (chunk previo/siguiente) |
| `media/` | imágenes y tablas que cita la respuesta |
| `raw_data/` | los PDF, para los enlaces "abrir en la página N" |

**No se sube `embeddings_data/`** (563 MB): son vectores intermedios que solo
usa el pipeline de ingesta, y la ingesta no corre en el servidor. Verificado
revisando qué rutas lee la API.

Si cambian los documentos, el flujo es: reindexar **en local**, y volver a
correr `upload-data.sh`.

---

## Operación

Todos los comandos necesitan el prefijo de los dos compose:

```bash
ssh -i deploy/unq-rag-key.pem ec2-user@<ip>
cd ~/unq-rag
C="docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml"

$C ps                 # estado
$C logs -f api        # logs
$C restart api        # reiniciar
$C down               # bajar
```

### Cambiar la contraseña del sitio

```bash
printf 'usuario:%s\n' "$(openssl passwd -apr1 'la-nueva')" > deploy/.htpasswd
scp -i deploy/unq-rag-key.pem deploy/.htpasswd ec2-user@<ip>:~/unq-rag/deploy/.htpasswd
ssh -i deploy/unq-rag-key.pem ec2-user@<ip> "cd ~/unq-rag && $C restart web"
```

### Dar de baja todo

```bash
aws ec2 terminate-instances --instance-ids <id>
aws ec2 delete-security-group --group-name unq-rag-sg
aws ec2 delete-key-pair --key-name unq-rag-key
```

---

## Decisiones y sus límites

**Una sola instancia, no ECS ni Lambda.** Chroma es SQLite embebido: la API
escribe el WAL para poder leer, y la caché de respuestas es una colección
dentro del mismo archivo. Eso significa **un solo escritor**, así que no hay
escalado horizontal posible y hace falta un filesystem real. Fargate con EFS
funcionaría, pero con una sola tarea, SQLite sobre NFS y más piezas: más costo
y más complejidad para el mismo resultado.

**`t3.small` alcanza.** Medido en local: 361 MB la API, 195 MB la línea base,
9 MB nginx — unos 570 MB de los 2 GB. El swap de 2 GB que configura el
user-data no es para correr, es para los **picos del build**: `npm ci` +
`vite build` y los `pip install` pasan de 1 GB y sin swap el OOM killer corta
el build.

**Basic auth en nginx, no `API_TOKEN`.** La API tiene soporte de token Bearer,
pero **el frontend no lo manda**: activarlo devolvería 401 en todas las
consultas. La basic auth resuelve lo mismo sin tocar código, y cubre además
`/api/media/`, por donde se descargan los PDF de los manuales.

**Sin HTTPS.** Al no haber dominio, queda `http://<ip>` y el navegador avisa
"No seguro". Dos consecuencias reales: la contraseña viaja en claro, y las
preguntas y respuestas también. Alcanza para una demo entre gente conocida; si
va a estar más tiempo o con más gente, conviene un subdominio y Let's Encrypt.

**Los PDF quedan accesibles** a quien tenga la contraseña. Son manuales de
fabricante (PowerFlex, TBEN) y una tesis; vale tenerlo presente según a quién
le compartas el acceso.

---

## Si algo falla

**`upload-data.sh` se queda esperando Docker.** El user-data todavía está
instalando:

```bash
ssh -i deploy/unq-rag-key.pem ec2-user@<ip> "sudo tail -50 /var/log/cloud-init-output.log"
```

**El build se corta sin mensaje claro.** Casi siempre es memoria. Comprobá que
el swap esté activo:

```bash
ssh -i deploy/unq-rag-key.pem ec2-user@<ip> "free -h; swapon --show"
```

**502 Bad Gateway.** nginx no llega a las APIs. Mirá si están arriba con
`$C ps`; si acabás de recrearlas, el resolver de nginx tarda hasta 10 s en
reresolver el DNS.

**Pide la contraseña y después da 500 Internal Server Error.** nginx no puede
leer el `.htpasswd`. Sus workers corren como el usuario `nginx` dentro del
contenedor, así que el archivo necesita permisos 644:

```bash
ssh -i deploy/unq-rag-key.pem ec2-user@<ip> "chmod 644 ~/unq-rag/deploy/.htpasswd"
```

En el log se ve como `open() "/etc/nginx/.htpasswd" failed (13: Permission
denied)`. No se reproduce en macOS: Docker Desktop monta por virtiofs y no
respeta el uid del host.

**El navegador no conecta, pero `docker compose ps` muestra todo healthy.**
nginx quedó publicado en 8080 en vez de 80, así que nadie lo alcanza desde
internet. Comprobá con `docker ps` que diga `0.0.0.0:80->80/tcp`; si dice 8080,
falta `WEB_PORT=80` en el `.env` del servidor.

**La app carga pero responde sin fuentes.** La API no está leyendo el índice.
Verificá que se subió:

```bash
ssh -i deploy/unq-rag-key.pem ec2-user@<ip> "ls -la ~/unq-rag/Ingestion/data/chroma_index/"
```

**No podés entrar por SSH.** Tu IP cambió. Volvé a correr `provision.sh`: añade
tu IP actual al security group.
