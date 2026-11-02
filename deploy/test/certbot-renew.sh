#!/bin/bash
set -euo pipefail
umask 077
exec 9>/run/lock/platform-live-editor-deploy.lock
flock -n 9 || exit 75
cd /opt/odoo20-test
bash platform-guard.sh >/dev/null
cd https
before=$(sha256sum letsencrypt/live/odoo.test.ai-data-platform.com/fullchain.pem)
image=$(cat certbot-image.txt)
docker run --rm --read-only --tmpfs /tmp --cap-drop ALL \
 --mount type=bind,source=/opt/odoo20-test/https/secrets/cloudflare.ini,target=/run/secrets/cloudflare.ini,readonly \
 --mount type=bind,source=/opt/odoo20-test/https/letsencrypt,target=/etc/letsencrypt \
 --mount type=bind,source=/opt/odoo20-test/https/work,target=/var/lib/letsencrypt \
 --mount type=bind,source=/opt/odoo20-test/https/logs,target=/var/log/letsencrypt \
 "$image" renew --non-interactive "$@"
after=$(sha256sum letsencrypt/live/odoo.test.ai-data-platform.com/fullchain.pem)
if [ "$before" != "$after" ]; then
    nginx -t
    systemctl reload nginx
fi
