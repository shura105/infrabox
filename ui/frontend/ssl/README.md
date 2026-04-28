# TLS certificates (gitignored)

This directory holds the nginx TLS certificate and private key, mounted into
the `infrabox-web` container at `/etc/nginx/ssl`. Files are environment-
specific (per developer / install) and **not** committed.

## Initial setup with mkcert

1. Install mkcert on your dev machine:
   ```sh
   brew install mkcert nss      # macOS
   sudo apt install mkcert       # Debian/Ubuntu
   ```

2. Create a local CA (one-time, system-wide):
   ```sh
   mkcert -install
   ```

3. Generate a cert for your homeServ hostnames + IP:
   ```sh
   cd ui/frontend/ssl/
   mkcert -cert-file infrabox.crt -key-file infrabox.key \
          homeServ2.local homeServ2 localhost 127.0.0.1 <LAN_IP>
   ```

4. Copy the root CA to share with phones / other devices:
   ```sh
   cp "$(mkcert -CAROOT)/rootCA.pem" .
   ```

5. Rebuild + restart the web container so it picks up the mounted certs:
   ```sh
   docker compose -f ui/docker-compose.yml up -d --build web
   ```

## Installing the root CA on iOS

1. Send `rootCA.pem` to the phone (AirDrop, email, whatever).
2. Open the file → "Profile downloaded".
3. Settings → General → VPN & Device Management → mkcert development CA → Install.
4. Settings → General → About → Certificate Trust Settings → enable
   the toggle next to "mkcert development CA".

After that the phone will trust HTTPS to `homeServ2.local` (and the IP) with
no warnings.
