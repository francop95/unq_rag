#!/usr/bin/env bash
#
# Sube el código y el índice a la instancia, construye las imágenes allá y
# levanta el stack.
#
#   ./deploy/upload-data.sh
#
# Lee la IP y la clave de deploy/.deploy-target, que dejó provision.sh.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
REMOTE_DIR="/home/ec2-user/unq-rag"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$HERE/.deploy-target" ] || die "falta deploy/.deploy-target. Corré primero: ./deploy/provision.sh"
# shellcheck disable=SC1091
. "$HERE/.deploy-target"
[ -n "${IP:-}" ] && [ -n "${KEY_FILE:-}" ] || die ".deploy-target incompleto"
[ -f "$HERE/.htpasswd" ] || die "falta deploy/.htpasswd. Lo genera provision.sh"
[ -f "$ROOT/.env" ] || die "falta el .env de la raíz con OPENAI_API_KEY"

SSH="ssh -i $KEY_FILE -o StrictHostKeyChecking=accept-new ec2-user@$IP"

say "Esperando que el user-data termine de instalar Docker"
# El wait de AWS solo garantiza que la VM arrancó, no que cloud-init terminó.
for i in $(seq 1 60); do
    if $SSH "test -f /home/ec2-user/.provision-done" 2>/dev/null; then
        info "Docker listo"
        break
    fi
    [ "$i" = "60" ] && die "el user-data no terminó en 5 minutos. Revisá: $SSH 'sudo tail -50 /var/log/cloud-init-output.log'"
    sleep 5
done

# ------------------------------------------------------------------- código
say "Subiendo el código"
# Solo lo que hace falta para construir y correr. Sin .venv, sin node_modules,
# sin los PDF originales (van aparte, con los datos).
rsync -az --delete \
  -e "ssh -i $KEY_FILE -o StrictHostKeyChecking=accept-new" \
  --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
  --exclude '.git' --exclude 'dist' --exclude 'data' \
  --exclude '.pytest_cache' --exclude '*.pem' \
  `# Los .env de cada servicio NO se suben. Solo el de la raíz, que va aparte` \
  `# con scp y chmod 600. Sin esta exclusión rsync copiaba API/.env e` \
  `# Ingestion/.env —los dos con la API key— y encima con sus permisos de` \
  `# origen (644), o sea legibles por cualquier usuario de la instancia.` \
  `# Ningún contenedor los monta: la API recibe la clave por environment y la` \
  `# ingesta no se despliega, así que eran copias del secreto sin ninguna` \
  `# función.` \
  --exclude '.env' --exclude '.env.*' --include '.env.example' \
  "$ROOT/API" "$ROOT/Frontend" "$ROOT/Ingestion" "$ROOT/deploy" \
  "$ROOT/docker-compose.yml" "$ROOT/.dockerignore" \
  "ec2-user@$IP:$REMOTE_DIR/"

# El .env va aparte y explícito: tiene la API key y rsync con --delete sobre
# patrones amplios es una forma fácil de subir un secreto sin querer.
say "Subiendo la configuración"
scp -q -i "$KEY_FILE" "$ROOT/.env" "ec2-user@$IP:$REMOTE_DIR/.env"
scp -q -i "$KEY_FILE" "$HERE/.htpasswd" "ec2-user@$IP:$REMOTE_DIR/deploy/.htpasswd"
# El .env a 600: ahí está la API key de OpenAI y solo la lee docker compose,
# que corre como ec2-user.
#
# El .htpasswd a 644, y no es un descuido: nginx lo abre desde sus workers, que
# corren como el usuario `nginx` (uid 101) dentro del contenedor. Con 600 el
# bind mount se lo niega y nginx responde 500 a TODA la app después de pedir la
# contraseña —"open() /etc/nginx/.htpasswd failed (13: Permission denied)"—, que
# es un síntoma bastante desconcertante.
#
# El archivo guarda un hash apr1, no la contraseña, y quien tenga shell en la
# instancia ya puede leer el .env con la API key, así que 644 no cambia el
# modelo de amenaza.
#
# Ojo si pruebas esto en macOS: ahí NO se reproduce. Docker Desktop monta por
# virtiofs y no respeta el uid del host, así que con 600 funciona igual. El
# fallo aparece solo en Linux, o sea en el servidor.
$SSH "chmod 600 $REMOTE_DIR/.env && chmod 644 $REMOTE_DIR/deploy/.htpasswd"

# En el servidor nginx va en el 80, no en el 8080 del .env local.
#
# El 8080 es el default local a propósito, para no chocar con otra cosa en la
# máquina de desarrollo. Pero acá el security group abre el 80, así que si se
# sube el .env tal cual, nginx escucha en 8080, nadie lo alcanza desde internet
# y el navegador da "no se puede conectar" con todo el stack aparentemente bien.
$SSH "cd $REMOTE_DIR && (grep -q '^WEB_PORT=' .env \
        && sed -i 's/^WEB_PORT=.*/WEB_PORT=80/' .env \
        || echo 'WEB_PORT=80' >> .env)"

# uid/gid del usuario remoto, para que los contenedores corran como el dueño de
# los datos del bind mount (ver la nota en docker-compose.prod.yml). Se detecta
# en vez de hardcodear 1000: en Amazon Linux ec2-user es 1000, pero en otra AMI
# o con otro usuario podría no serlo.
REMOTE_UID=$($SSH "id -u")
REMOTE_GID=$($SSH "id -g")
$SSH "cd $REMOTE_DIR \
      && (grep -q '^APP_UID=' .env && sed -i \"s/^APP_UID=.*/APP_UID=$REMOTE_UID/\" .env || echo \"APP_UID=$REMOTE_UID\" >> .env) \
      && (grep -q '^APP_GID=' .env && sed -i \"s/^APP_GID=.*/APP_GID=$REMOTE_GID/\" .env || echo \"APP_GID=$REMOTE_GID\" >> .env)"
info "hecho (WEB_PORT=80, APP_UID=$REMOTE_UID en el servidor)"

# -------------------------------------------------------------------- datos
say "Subiendo el índice y la media (~230 MB)"
# Lo que la API necesita en runtime, y nada más:
#   chroma_index*   los índices vectoriales
#   chunks_data*    los lee el context expander (prev/next chunk)
#   media           imágenes y tablas que cita la respuesta
#   raw_data        los PDF, para los enlaces "abrir en la página N"
#
# embeddings_data* NO se sube: son 563 MB de vectores intermedios que solo usa
# el pipeline de ingesta, y la ingesta no corre en el servidor.
$SSH "mkdir -p $REMOTE_DIR/Ingestion/data"
for d in chroma_index chroma_index_baseline chunks_data chunks_data_baseline media raw_data; do
    if [ -d "$ROOT/Ingestion/data/$d" ]; then
        info "$d"
        rsync -az -e "ssh -i $KEY_FILE" \
          "$ROOT/Ingestion/data/$d" "ec2-user@$IP:$REMOTE_DIR/Ingestion/data/"
    fi
done
for f in ingestion_manifest.json baseline_manifest.json; do
    [ -f "$ROOT/Ingestion/data/$f" ] && \
      scp -q -i "$KEY_FILE" "$ROOT/Ingestion/data/$f" "ec2-user@$IP:$REMOTE_DIR/Ingestion/data/"
done

# ------------------------------------------------------------------- build
say "Construyendo las imágenes en el servidor (5-8 min)"
info "solo web, api y api-baseline: la ingesta no se despliega"
$SSH "cd $REMOTE_DIR && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build web api api-baseline"

say "Levantando el stack"
$SSH "cd $REMOTE_DIR && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d web api api-baseline"

say "Estado"
$SSH "cd $REMOTE_DIR && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml ps"

echo
say "Desplegado"
info "URL : http://$IP"
info "      (pide el usuario y contraseña que definiste en provision.sh)"
echo
info "Logs    : $SSH 'cd $REMOTE_DIR && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml logs -f api'"
info "Reiniciar: $SSH 'cd $REMOTE_DIR && docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml restart'"
