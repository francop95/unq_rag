#!/usr/bin/env bash
#
# Dónde está la instancia, ahora mismo.
#
# Se usa con `source`, no se ejecuta.
#
# Antes la IP se leía de deploy/.deploy-target, escrita cuando se creó la
# instancia. Eso alcanzaba mientras estuvo prendida para siempre, pero con el
# despertador la instancia se apaga sola y la IP pública se libera: en el
# siguiente encendido es otra, y el archivo queda mintiendo. Un deploy contra
# la IP vieja no rompe nada —falla al conectar— pero falla sin decir por qué.
#
# Así que la IP se pregunta a EC2 cada vez. Si no hay AWS CLI o falta el
# INSTANCE_ID, se cae a la del archivo, que es como funcionaba antes.

# Imprime la IP pública actual. Con `encender` como primer argumento, además
# prende la instancia y espera a que conteste por SSH.
ip_actual() {
    local encender="${1:-}"

    if [ -z "${INSTANCE_ID:-}" ] || [ -z "${REGION:-}" ] \
       || ! command -v aws >/dev/null 2>&1; then
        printf '%s' "${IP:-}"
        return
    fi

    local estado
    estado=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo "")

    if [ "$estado" != "running" ] && [ "$encender" = "encender" ]; then
        printf '    la instancia está "%s"; encendiéndola…\n' "${estado:-desconocida}" >&2
        aws ec2 start-instances --region "$REGION" --instance-ids "$INSTANCE_ID" >/dev/null
        aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
    fi

    aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null \
        | grep -v '^None$' || printf '%s' "${IP:-}"
}

# La IP, con la instancia encendida y aceptando SSH. Es lo que necesita
# cualquier script que vaya a copiar archivos.
ip_lista_para_ssh() {
    local ip
    ip=$(ip_actual encender)
    [ -n "$ip" ] || { printf '\n\033[31mERROR: no pude averiguar la IP\033[0m\n' >&2; return 1; }

    # Encendida no es lo mismo que lista: sshd tarda unos segundos más.
    local i
    for i in $(seq 1 30); do
        if ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new \
               -o ConnectTimeout=8 -o BatchMode=yes "ec2-user@$ip" true 2>/dev/null; then
            printf '%s' "$ip"
            return 0
        fi
        sleep 5
    done
    printf '\n\033[31mERROR: %s no acepta SSH\033[0m\n' "$ip" >&2
    return 1
}
