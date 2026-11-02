#!/bin/bash
# Consistent backup of this isolated test instance only.
set -euo pipefail
umask 077
cd /opt/odoo20-test
exec 8>/run/lock/odoo20-backup.lock
flock -n 8 || { echo 'Another Odoo backup is active'; exit 75; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
destination="backups/$stamp"
mkdir -p backups
mkdir "$destination"
was_running=$(docker compose ps --status running --services | grep -cx app || true)
resume() { if [ "$was_running" = 1 ]; then docker compose start app >/dev/null; fi; }
trap resume EXIT
docker compose stop app >/dev/null
docker compose exec -T db pg_dump -U postgres --format=custom odoo20 > "$destination/database.dump"
docker compose run --rm --no-deps -T --entrypoint tar app -czf - -C /var/lib/odoo . > "$destination/filestore.tar.gz"
docker compose exec -T db pg_restore --list < "$destination/database.dump" > "$destination/database.toc"
tar -tzf "$destination/filestore.tar.gz" >/dev/null
cp image-id.txt deployment.sha256 "$destination/"
(cd "$destination" && sha256sum database.dump filestore.tar.gz > SHA256SUMS)
aws s3 cp "$destination/" "s3://platform-test-db-backups-243124497044/odoo20/$stamp/" --recursive --only-show-errors
printf 'Backup verified and uploaded: %s\n' "$stamp"
