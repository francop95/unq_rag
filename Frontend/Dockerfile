# Frontend: se compila con Node y se sirve con nginx.
#
# La imagen final no lleva Node ni node_modules: solo el bundle estático y
# nginx, que además hace de proxy inverso a las dos APIs (ver nginx.conf).

# ---------- build ----------
FROM node:22-alpine AS build

# Rutas RELATIVAS a propósito. Vite reemplaza las VITE_* en tiempo de build, así
# que una URL absoluta quedaría grabada en el bundle y ataría la imagen a un
# host. Con /api y /api-baseline, nginx resuelve a dónde van y la misma imagen
# sirve en cualquier parte.
ARG VITE_API_BASE_URL=/api
ARG VITE_BASELINE_API_BASE_URL=/api-baseline
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_BASELINE_API_BASE_URL=$VITE_BASELINE_API_BASE_URL

WORKDIR /build

# Las dependencias en su propia capa: package-lock cambia mucho menos que el
# código, así que el npm ci no se repite en cada build.
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci

COPY Frontend/ ./
# `npm run build` corre `tsc -b && vite build`, así que un error de tipos
# rompe el build de la imagen. Es deliberado: mejor acá que en runtime.
RUN npm run build

# ---------- runtime ----------
FROM nginx:1.27-alpine

COPY Frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

# Archivo de autenticación vacío: en local no se pide contraseña. El deploy lo
# sobrescribe montando deploy/auth.conf encima. Tiene que existir igual, porque
# un `include` que apunta a un archivo inexistente impide arrancar nginx.
RUN touch /etc/nginx/auth-included.conf

EXPOSE 80

# nginx:alpine ya trae un CMD y un usuario adecuados; no hace falta tocarlos.
