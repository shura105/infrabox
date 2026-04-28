FROM nginx:stable-alpine

# Cert and key are mounted at runtime from ./ssl/ — see docker-compose.
# To regenerate locally:
#   mkdir -p ssl && cd ssl && \
#   mkcert -cert-file infrabox.crt -key-file infrabox.key \
#          homeServ2.local homeServ2 localhost 127.0.0.1 <LAN_IP>
RUN mkdir -p /etc/nginx/ssl
