FROM nginx:stable-alpine

# openssl для self-signed cert
RUN apk add --no-cache openssl

# генеруємо self-signed cert при білді (10 років, CN=infrabox)
RUN mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout /etc/nginx/ssl/infrabox.key \
      -out    /etc/nginx/ssl/infrabox.crt \
      -subj   "/C=UA/O=infrabox/CN=infrabox"
