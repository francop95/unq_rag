#!/usr/bin/env bash
#
# Crea la infraestructura mínima en AWS para el despliegue de prueba:
# un security group, un par de claves y una instancia EC2 con Docker listo.
#
#   ./deploy/provision.sh
#
# Idempotente: si el security group, la clave o la instancia ya existen, los
# reutiliza en vez de duplicarlos. Podés correrlo dos veces sin romper nada.
#
# NO sube el código ni los datos: eso es deploy/upload-data.sh.
set -euo pipefail

# ----------------------------------------------------------------- parámetros
# Respeta la región que tengas configurada en el AWS CLI. Antes esto defaulteaba
# a us-east-1 y creaba la instancia ahí aunque hubieras configurado otra, que es
# confuso y además cambia lo que te cobran.
REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"
NAME="${DEPLOY_NAME:-unq-rag}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
# 30 GB: las imágenes ocupan ~1 GB (api) + 77 MB (web), el índice ~230 MB, y el
# resto es margen para logs y capas intermedias de Docker.
DISK_GB="${DISK_GB:-30}"

KEY_NAME="$NAME-key"
SG_NAME="$NAME-sg"
KEY_FILE="$(cd "$(dirname "$0")" && pwd)/$KEY_NAME.pem"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ------------------------------------------------------------ comprobaciones
command -v aws >/dev/null 2>&1 || die "falta el AWS CLI. En macOS: brew install awscli"
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "el AWS CLI no tiene credenciales válidas. Corré: aws configure"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
say "Cuenta AWS $ACCOUNT · región $REGION · instancia $INSTANCE_TYPE"

# -------------------------------------------------------------- basic auth
HTPASSWD="$(cd "$(dirname "$0")" && pwd)/.htpasswd"
if [ ! -f "$HTPASSWD" ]; then
    say "Credenciales de acceso al sitio (basic auth)"
    read -rp "    usuario: " AUTH_USER
    read -rsp "    contraseña: " AUTH_PASS; echo
    [ -n "$AUTH_USER" ] && [ -n "$AUTH_PASS" ] || die "usuario y contraseña no pueden estar vacíos"
    # apr1 es el formato que entiende nginx y que openssl genera en cualquier OS.
    printf '%s:%s\n' "$AUTH_USER" "$(openssl passwd -apr1 "$AUTH_PASS")" > "$HTPASSWD"
    # 644 y no 600: nginx lo lee desde sus workers, que corren como otro usuario
    # dentro del contenedor (ver la nota en upload-data.sh). Guarda un hash, no
    # la contraseña.
    chmod 644 "$HTPASSWD"
    info "guardado en deploy/.htpasswd (está en .gitignore)"
else
    info "deploy/.htpasswd ya existe, se reutiliza"
fi

# --------------------------------------------------------------- par de claves
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" >/dev/null 2>&1; then
    info "el par de claves $KEY_NAME ya existe"
    [ -f "$KEY_FILE" ] || die "existe $KEY_NAME en AWS pero falta $KEY_FILE.
    Borrá la clave en AWS y volvé a correr el script, o copiá el .pem a deploy/"
else
    say "Creando par de claves $KEY_NAME"
    aws ec2 create-key-pair --key-name "$KEY_NAME" --region "$REGION" \
        --query KeyMaterial --output text > "$KEY_FILE"
    chmod 400 "$KEY_FILE"
    info "clave privada en $KEY_FILE — sin este archivo no hay SSH, guardalo"
fi

# ------------------------------------------------------------ security group
MI_IP=$(curl -s --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')
[ -n "$MI_IP" ] || die "no se pudo averiguar tu IP pública"

SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
          --filters "Name=group-name,Values=$SG_NAME" \
          --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    say "Creando security group $SG_NAME"
    SG_ID=$(aws ec2 create-security-group --region "$REGION" \
              --group-name "$SG_NAME" \
              --description "unq-rag: HTTP publico, SSH restringido" \
              --query GroupId --output text)
    # 80 abierto: es el único puerto que sale a internet, y detrás tiene basic
    # auth. Los 5000/5001 quedan en loopback dentro de la instancia.
    aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
        --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
    info "puerto 80 abierto a internet (con basic auth por delante)"
else
    info "el security group $SG_NAME ya existe ($SG_ID)"
fi

# SSH solo desde tu IP actual. Si cambia, volvé a correr el script.
if aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
     --protocol tcp --port 22 --cidr "$MI_IP/32" >/dev/null 2>&1; then
    info "SSH habilitado desde $MI_IP/32"
else
    info "SSH desde $MI_IP/32 ya estaba habilitado"
fi

# ---------------------------------------------------------------------- AMI
say "Buscando la última Amazon Linux 2023"
# Primero por SSM, que es el método recomendado. Si el usuario IAM no tiene
# permiso de ssm:GetParameters (AmazonEC2FullAccess no lo incluye), se cae a
# describe-images, que solo necesita permisos de EC2. Así alcanza con una única
# policy administrada y el alta de la cuenta es más simple.
AMI_ID=$(aws ssm get-parameters --region "$REGION" \
    --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query 'Parameters[0].Value' --output text 2>/dev/null || echo "")

if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
    info "sin permiso de SSM, buscando la AMI por describe-images"
    AMI_ID=$(aws ec2 describe-images --region "$REGION" --owners amazon \
        --filters "Name=name,Values=al2023-ami-2023.*-x86_64" \
                  "Name=state,Values=available" \
                  "Name=architecture,Values=x86_64" \
        --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text 2>/dev/null || echo "")
fi

[ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ] || die "no se pudo resolver la AMI de Amazon Linux 2023.
    Revisá que el usuario IAM tenga permisos de EC2 y que la región $REGION sea correcta."
info "$AMI_ID"

# ----------------------------------------------------------------- instancia
EXISTING=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=$NAME" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")

if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
    INSTANCE_ID="$EXISTING"
    info "ya existe la instancia $INSTANCE_ID con el tag Name=$NAME, se reutiliza"
    STATE=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
              --query 'Reservations[0].Instances[0].State.Name' --output text)
    if [ "$STATE" = "stopped" ]; then
        info "estaba apagada, arrancándola"
        aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
    fi
else
    say "Lanzando la instancia"
    # user-data: prepara Docker y el swap. Corre una sola vez, al primer arranque.
    USER_DATA=$(cat <<'CLOUDINIT'
#!/bin/bash
set -x
dnf update -y
dnf install -y docker rsync

# El plugin de compose no viene en los repos de AL2023: se baja el binario.
COMPOSE_VERSION=v2.32.4
mkdir -p /usr/libexec/docker/cli-plugins
curl -fsSL -o /usr/libexec/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
chmod +x /usr/libexec/docker/cli-plugins/docker-compose

systemctl enable --now docker
usermod -aG docker ec2-user

# 2 GB de swap. La instancia tiene 2 GB de RAM y el stack usa ~570 MB, así que
# sobra para correr; el swap es para los PICOS DEL BUILD: `npm ci` + vite build
# y los pip install se comen más de 1 GB y sin esto el OOM killer corta el build.
if [ ! -f /swapfile ]; then
    dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

mkdir -p /home/ec2-user/unq-rag
chown ec2-user:ec2-user /home/ec2-user/unq-rag
touch /home/ec2-user/.provision-done
CLOUDINIT
)
    INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" \
        --image-id "$AMI_ID" \
        --instance-type "$INSTANCE_TYPE" \
        --key-name "$KEY_NAME" \
        --security-group-ids "$SG_ID" \
        --user-data "$USER_DATA" \
        --block-device-mappings "[{\"DeviceName\":\"/dev/xvda\",\"Ebs\":{\"VolumeSize\":$DISK_GB,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
        --query 'Instances[0].InstanceId' --output text)
    info "instancia $INSTANCE_ID"
fi

say "Esperando que la instancia esté lista"
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
      --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# Se guarda para que upload-data.sh no tenga que preguntar nada.
cat > "$(cd "$(dirname "$0")" && pwd)/.deploy-target" <<EOF
INSTANCE_ID=$INSTANCE_ID
REGION=$REGION
IP=$IP
KEY_FILE=$KEY_FILE
EOF

say "Listo"
info "instancia : $INSTANCE_ID"
info "IP        : $IP"
info "SSH       : ssh -i $KEY_FILE ec2-user@$IP"
echo
info "El user-data está instalando Docker en segundo plano (1-2 min)."
info "Cuando termine, el siguiente paso es:"
info "  ./deploy/upload-data.sh"
