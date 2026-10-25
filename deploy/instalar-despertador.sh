#!/usr/bin/env bash
#
# Deja la demo encendiéndose sola cuando alguien entra, y apagándose cuando
# nadie la usa.
#
#   ./deploy/instalar-despertador.sh
#
# Crea una Lambda con Function URL que valida la contraseña, enciende la
# instancia si está apagada y después le hace de proxy; y en la instancia, un
# timer que la apaga tras 20 minutos sin pedidos.
#
# Por qué baja la cuenta: la demo se usa un día de cada cuatro y el 89% de los
# ~21 USD/mes es tiempo encendido. Apagada quedan ~3,50 (el disco, que se paga
# igual, más las horas que de verdad se use). La Lambda entra en la capa
# gratuita.
#
# Idempotente: si la función, el rol o la URL ya existen, los actualiza.
#
# HACE FALTA UNA CUENTA CON PERMISOS DE ADMIN, una sola vez. El usuario
# unq-rag-deploy solo tiene EC2, y esto crea un rol de IAM y una Lambda:
#
#   AWS_PROFILE=admin ./deploy/instalar-despertador.sh
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$AQUI/.deploy-target"
NAME="${DEPLOY_NAME:-unq-rag}"
FUNCION="$NAME-despertador"
ROL="$NAME-despertador-rol"
MINUTOS_OCIOSO="${MINUTOS_OCIOSO:-20}"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------ comprobaciones
command -v aws >/dev/null 2>&1 || die "falta el AWS CLI"
command -v openssl >/dev/null 2>&1 || die "falta openssl"
[ -f "$DESTINO" ] || die "no encuentro $DESTINO. Corré antes deploy/provision.sh"

# shellcheck disable=SC1090
source "$DESTINO"
: "${INSTANCE_ID:?falta INSTANCE_ID en .deploy-target}"
: "${REGION:?falta REGION en .deploy-target}"
: "${KEY_FILE:?falta KEY_FILE en .deploy-target}"

aws sts get-caller-identity >/dev/null 2>&1 || die "el AWS CLI no tiene credenciales válidas"
CUENTA=$(aws sts get-caller-identity --query Account --output text)

say "Cuenta $CUENTA · región $REGION · instancia $INSTANCE_ID"

# Se chequea antes de empezar para no dejar medio creado lo que no se puede
# terminar: el usuario de deploy no tiene Lambda ni IAM, y el error aparecería
# recién después de haber creado el rol.
aws lambda list-functions --region "$REGION" --max-items 1 >/dev/null 2>&1 \
    || die "estas credenciales no pueden usar Lambda.
    Corré esto una vez con una cuenta de admin:
        AWS_PROFILE=admin $0"

# -------------------------------------------------------------- la contraseña
HTPASSWD="$AQUI/.htpasswd"
[ -f "$HTPASSWD" ] || die "no encuentro $HTPASSWD. Corré antes deploy/provision.sh"

say "Contraseña de acceso"
info "Tiene que ser LA MISMA que pide el sitio hoy: la Lambda reenvía la"
info "cabecera a nginx, y si no coinciden el navegador la pide dos veces."
read -rp "    usuario: " AUTH_USER
read -rsp "    contraseña: " AUTH_PASS; echo
[ -n "$AUTH_USER" ] && [ -n "$AUTH_PASS" ] || die "no pueden estar vacíos"

# Se verifica contra el .htpasswd que ya usa nginx, en vez de confiar en que
# uno se acordó bien. apr1 guarda la sal entre el segundo y el tercer '$'.
GUARDADO=$(grep "^${AUTH_USER}:" "$HTPASSWD" | head -1 | cut -d: -f2-)
[ -n "$GUARDADO" ] || die "el usuario '$AUTH_USER' no está en deploy/.htpasswd"
SAL_APR1=$(printf '%s' "$GUARDADO" | cut -d'$' -f3)
if [ "$(openssl passwd -apr1 -salt "$SAL_APR1" "$AUTH_PASS")" != "$GUARDADO" ]; then
    die "esa contraseña no es la que tiene nginx en deploy/.htpasswd"
fi
info "coincide con la que pide el sitio"

# PBKDF2 para la Lambda. No se guarda la contraseña en una variable de
# entorno: cualquiera con acceso a la consola de Lambda las ve en claro.
SAL=$(openssl rand -hex 16)
VUELTAS=200000
HASH=$(python3 -c "
import hashlib, sys
print(hashlib.pbkdf2_hmac('sha256', sys.argv[1].encode(), sys.argv[2].encode(),
                          int(sys.argv[3]), 32).hex())
" "$AUTH_USER:$AUTH_PASS" "$SAL" "$VUELTAS")

# ------------------------------------------------------------------- el rol
say "Rol de IAM"
POLITICA_CONFIANZA='{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
  "Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if aws iam get-role --role-name "$ROL" >/dev/null 2>&1; then
    info "$ROL ya existe, se reutiliza"
else
    aws iam create-role --role-name "$ROL" \
        --assume-role-policy-document "$POLITICA_CONFIANZA" \
        --description "Deja que la puerta de $NAME encienda la instancia" >/dev/null
    info "creado $ROL"
fi

# DescribeInstances no admite permisos por recurso, así que va con "*".
# StartInstances sí, y se limita a esta instancia: si la función se
# equivocara de id, no puede encender ninguna otra.
aws iam put-role-policy --role-name "$ROL" --policy-name "encender-$NAME" \
    --policy-document "{
      \"Version\": \"2012-10-17\",
      \"Statement\": [
        {\"Effect\": \"Allow\", \"Action\": \"ec2:DescribeInstances\", \"Resource\": \"*\"},
        {\"Effect\": \"Allow\", \"Action\": \"ec2:StartInstances\",
         \"Resource\": \"arn:aws:ec2:$REGION:$CUENTA:instance/$INSTANCE_ID\"},
        {\"Effect\": \"Allow\",
         \"Action\": [\"logs:CreateLogGroup\", \"logs:CreateLogStream\", \"logs:PutLogEvents\"],
         \"Resource\": \"arn:aws:logs:$REGION:$CUENTA:*\"}
      ]}" >/dev/null
info "permisos: describir cualquier instancia, encender solo $INSTANCE_ID"

ROL_ARN=$(aws iam get-role --role-name "$ROL" --query Role.Arn --output text)

# --------------------------------------------------------------- la función
say "Lambda $FUNCION"
PAQUETE=$(mktemp -d)/despertador.zip
(cd "$AQUI/despertador" && zip -q "$PAQUETE" index.mjs)

ENTORNO="Variables={INSTANCE_ID=$INSTANCE_ID,AUTH_SALT=$SAL,AUTH_HASH=$HASH,AUTH_ITER=$VUELTAS}"

if aws lambda get-function --function-name "$FUNCION" --region "$REGION" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$FUNCION" --region "$REGION" \
        --zip-file "fileb://$PAQUETE" >/dev/null
    aws lambda wait function-updated --function-name "$FUNCION" --region "$REGION"
    aws lambda update-function-configuration --function-name "$FUNCION" --region "$REGION" \
        --environment "$ENTORNO" --timeout 30 --memory-size 256 >/dev/null
    aws lambda wait function-updated --function-name "$FUNCION" --region "$REGION"
    info "actualizada"
else
    # El rol recién creado tarda unos segundos en propagarse; sin reintento,
    # el primer create-function falla con InvalidParameterValueException.
    for intento in 1 2 3 4 5 6; do
        if aws lambda create-function --function-name "$FUNCION" --region "$REGION" \
            --runtime nodejs22.x --handler index.handler --role "$ROL_ARN" \
            --zip-file "fileb://$PAQUETE" --timeout 30 --memory-size 256 \
            --environment "$ENTORNO" >/dev/null 2>&1; then
            info "creada"
            break
        fi
        [ "$intento" = 6 ] && die "no se pudo crear la función"
        info "esperando a que se propague el rol ($intento/6)…"
        sleep 5
    done
    aws lambda wait function-active --function-name "$FUNCION" --region "$REGION"
fi

# ------------------------------------------------------------------ la URL
say "Function URL"
# RESPONSE_STREAM y no BUFFERED: sin streaming el techo son 6 MB de payload,
# y el cuerpo binario viaja en base64, así que el PDF de 5,4 MB que se sirve
# por /media no entra. Con streaming el techo sube a 20 MB.
if aws lambda get-function-url-config --function-name "$FUNCION" --region "$REGION" >/dev/null 2>&1; then
    URL=$(aws lambda update-function-url-config --function-name "$FUNCION" --region "$REGION" \
        --auth-type NONE --invoke-mode RESPONSE_STREAM --query FunctionUrl --output text)
else
    URL=$(aws lambda create-function-url-config --function-name "$FUNCION" --region "$REGION" \
        --auth-type NONE --invoke-mode RESPONSE_STREAM --query FunctionUrl --output text)
fi

# AuthType NONE significa "sin firma de IAM", no "sin contraseña": la
# contraseña la valida la propia función, que es lo que impide que un bot
# encienda la instancia.
aws lambda add-permission --function-name "$FUNCION" --region "$REGION" \
    --statement-id url-publica --action lambda:InvokeFunctionUrl \
    --principal "*" --function-url-auth-type NONE >/dev/null 2>&1 || true
info "$URL"

# Por defecto los logs de Lambda no caducan nunca. Son centavos, pero crecen
# para siempre y nadie los vuelve a mirar después de la primera semana.
aws logs create-log-group --region "$REGION" \
    --log-group-name "/aws/lambda/$FUNCION" >/dev/null 2>&1 || true
aws logs put-retention-policy --region "$REGION" \
    --log-group-name "/aws/lambda/$FUNCION" --retention-in-days 14 >/dev/null 2>&1 \
    && info "logs: se borran a los 14 días"

# ------------------------------------------- el apagado, dentro de la instancia
say "Apagado por inactividad en la instancia"
# shellcheck disable=SC1091
source "$AQUI/lib-instancia.sh"
IP=$(ip_lista_para_ssh) || exit 1
SSH="ssh -i $KEY_FILE -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 ec2-user@$IP"
info "instancia en $IP"

scp -q -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new \
    "$AQUI/despertador/apagar-si-ocioso.sh" "ec2-user@$IP:/tmp/apagar-si-ocioso.sh"

$SSH "sudo install -m 755 /tmp/apagar-si-ocioso.sh /usr/local/bin/apagar-si-ocioso && \
sudo tee /etc/systemd/system/apagar-si-ocioso.service >/dev/null <<'UNIT'
[Unit]
Description=Apaga la instancia si nadie la usa

[Service]
Type=oneshot
Environment=MINUTOS_OCIOSO=$MINUTOS_OCIOSO
ExecStart=/usr/local/bin/apagar-si-ocioso
UNIT
sudo tee /etc/systemd/system/apagar-si-ocioso.timer >/dev/null <<'UNIT'
[Unit]
Description=Revisa cada 5 minutos si la instancia está ociosa

[Timer]
OnBootSec=10min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload && sudo systemctl enable --now apagar-si-ocioso.timer" >/dev/null

info "se apaga tras $MINUTOS_OCIOSO min sin pedidos (no mientras haya alguien por SSH)"

# ------------------------------------------------------------------ prueba
say "Prueba"
SIN_CLAVE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$URL")
CON_CLAVE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 -u "$AUTH_USER:$AUTH_PASS" "$URL")
info "sin contraseña  → $SIN_CLAVE  (tiene que ser 401: es lo que frena a los bots)"
info "con contraseña  → $CON_CLAVE  (200 si ya está arriba, 503 mientras enciende)"

[ "$SIN_CLAVE" = "401" ] || die "sin contraseña devolvió $SIN_CLAVE y debería ser 401"

say "Listo"
info "Entrá por:  $URL"
info ""
info "La IP de la instancia cambia en cada encendido, así que esa URL es la"
info "que hay que compartir. El sitio viejo por IP deja de servir."
info ""
info "Para apagarla ahora y empezar a ahorrar:"
info "    aws ec2 stop-instances --region $REGION --instance-ids $INSTANCE_ID"
