#!/bin/bash
# Nightly off-VM backup: dumps the Postgres database and archives the media
# directory, then uploads both to Azure Blob Storage (storage account
# "meritlense", container "backups") via the VM's managed identity - no
# storage key is stored on disk. A 30-day lifecycle policy on the container
# handles retention/expiry, so this script does no cleanup of old backups
# itself. Independent of this VM: protects against total VM/disk loss, not
# just accidental deletion.
set -euo pipefail

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
APP_DIR=/home/azureuser/meritlense
TMP_DIR=$(mktemp -d /tmp/meritlense-backup-XXXXXX)
STORAGE_ACCOUNT=meritlense
CONTAINER=backups

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# Read only the specific keys needed, rather than sourcing the whole .env,
# so nothing in it is ever executed as shell code.
env_var() { grep -E "^$1=" "$APP_DIR/.env" | head -n1 | cut -d= -f2-; }
DB_HOST=$(env_var DB_HOST)
DB_PORT=$(env_var DB_PORT)
DB_NAME=$(env_var DB_NAME)
DB_USER=$(env_var DB_USER)
DB_PASSWORD=$(env_var DB_PASSWORD)

DB_DUMP="$TMP_DIR/db_${TIMESTAMP}.dump"
MEDIA_ARCHIVE="$TMP_DIR/media_${TIMESTAMP}.tar.gz"

PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -Fc "$DB_NAME" -f "$DB_DUMP"

if [ -d "$APP_DIR/media" ]; then
  tar czf "$MEDIA_ARCHIVE" -C "$APP_DIR" media
else
  tar czf "$MEDIA_ARCHIVE" --files-from /dev/null
fi

TOKEN=$(curl -s -H "Metadata:true" \
  "http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F" \
  | jq -r .access_token)

upload_blob() {
  local file="$1" blob_path="$2" size status
  size=$(stat -c%s "$file")
  status=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
    "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${CONTAINER}/${blob_path}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "x-ms-version: 2021-08-06" \
    -H "x-ms-blob-type: BlockBlob" \
    -H "Content-Length: ${size}" \
    --data-binary "@${file}")
  if [ "$status" != "201" ]; then
    echo "FAILED uploading $blob_path (HTTP $status)" >&2
    return 1
  fi
  echo "Uploaded $blob_path (${size} bytes)"
}

upload_blob "$DB_DUMP" "db/${TIMESTAMP}.dump"
upload_blob "$MEDIA_ARCHIVE" "media/${TIMESTAMP}.tar.gz"

echo "Backup ${TIMESTAMP} completed successfully."
