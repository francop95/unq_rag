#!/usr/bin/env bash
#
# Apaga la instancia cuando nadie la está usando.
#
# Es la otra mitad del ahorro: la Lambda la enciende, esto la apaga. Sin esto
# la instancia queda prendida para siempre después de la primera visita y
# volvés a pagar los 21 USD/mes.
#
# Corre cada 5 minutos por un timer de systemd (lo instala
# deploy/instalar-despertador.sh). Se apaga con `shutdown -h`, que en esta
# instancia la deja en `stopped` y no terminada, porque
# InstanceInitiatedShutdownBehavior está en "stop".
#
# El uso se mide por el log de nginx y no por carga de CPU: la demo pasa casi
# todo el tiempo esperando a OpenAI, así que la CPU está en 0,05 incluso
# mientras alguien la está usando, y una alarma por CPU la apagaría en la cara
# del visitante.
set -uo pipefail

MINUTOS_OCIOSO="${MINUTOS_OCIOSO:-20}"
# Margen desde el arranque. El primer visitante espera ~1 minuto a que
# levante, y recién después hace su primer pedido: sin esta gracia, una
# instancia recién encendida se apaga antes de que llegue a usarla.
GRACIA_MIN="${GRACIA_MIN:-10}"
CONTENEDOR="${CONTENEDOR:-unq-rag-web-1}"

# Con SOLO_MIRAR=1 dice qué haría y no apaga nada. Es como se prueba sin
# tirarle la demo a alguien encima:
#   ssh ... 'SOLO_MIRAR=1 bash -s' < deploy/despertador/apagar-si-ocioso.sh
SOLO_MIRAR="${SOLO_MIRAR:-}"

avisar() { logger -t despertador "$*" 2>/dev/null || true; }
decidir() {
    if [ -n "$SOLO_MIRAR" ]; then
        printf 'seguiría prendida: %s\n' "$1"
    fi
    exit 0
}

UPTIME_MIN=$(awk '{print int($1/60)}' /proc/uptime)
if [ "$UPTIME_MIN" -lt "$GRACIA_MIN" ]; then
    decidir "recién encendida (${UPTIME_MIN} min < ${GRACIA_MIN} de gracia)"
fi

# Si hay alguien por SSH es porque se está desplegando o mirando algo. Apagar
# en medio del rsync de upload-data.sh deja el índice a medio subir.
#
# Se miran las conexiones establecidas al puerto 22 y no `who`: en Amazon
# Linux un `ssh host comando` —que es como corren rsync, scp y los scripts de
# deploy— no deja entrada en utmp, así que `who` devuelve vacío en el caso
# exacto que esto tiene que proteger. Comprobado sobre esta instancia.
CONEXIONES=$(/usr/sbin/ss -tn state established '( sport = :22 )' 2>/dev/null | tail -n +2 | wc -l)
if [ "${CONEXIONES:-0}" -gt 0 ]; then
    decidir "hay $CONEXIONES sesión(es) SSH abiertas (¿un deploy?)"
fi

if ! docker inspect "$CONTENEDOR" >/dev/null 2>&1; then
    avisar "no encuentro el contenedor $CONTENEDOR; no apago por las dudas"
    decidir "no encuentro el contenedor $CONTENEDOR"
fi

# Cualquier línea del access log sirve: nginx solo registra lo que entra por
# el puerto 80, y los healthchecks van directo a los contenedores de la API.
PEDIDOS=$(docker logs --since "${MINUTOS_OCIOSO}m" "$CONTENEDOR" 2>&1 | grep -c 'HTTP/1' || true)
if [ "${PEDIDOS:-0}" -gt 0 ]; then
    decidir "$PEDIDOS pedido(s) en los últimos ${MINUTOS_OCIOSO} min"
fi

if [ -n "$SOLO_MIRAR" ]; then
    printf 'APAGARÍA: sin pedidos en %s min (uptime %s min)\n' "$MINUTOS_OCIOSO" "$UPTIME_MIN"
    exit 0
fi

avisar "sin pedidos en ${MINUTOS_OCIOSO} min (uptime ${UPTIME_MIN} min); apagando"
/sbin/shutdown -h now "apagado por inactividad"
