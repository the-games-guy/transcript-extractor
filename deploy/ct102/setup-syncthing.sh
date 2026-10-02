#!/usr/bin/env bash
# Install native Syncthing on CT102 for the yt-transcripts folder.
# Run on the Proxmox HOST as root:   bash setup-syncthing.sh [CTID]
# Safe to re-run.
set -euo pipefail

CTID="${1:-102}"
OUT=/srv/yt-transcripts
KEYRING=/usr/share/keyrings/syncthing-archive-keyring.gpg

ct() { pct exec "$CTID" -- "$@"; }

echo "==> CT$CTID: prerequisites"
ct apt-get update -qq
ct apt-get install -y -qq curl gnupg ca-certificates

echo "==> CT$CTID: Syncthing apt repo + package"
if ! ct test -s "$KEYRING"; then
  ct bash -c "curl -fsSL https://syncthing.net/release-key.gpg | gpg --dearmor -o $KEYRING"
fi
ct bash -c "echo 'deb [signed-by=$KEYRING] https://apt.syncthing.net/ syncthing stable' > /etc/apt/sources.list.d/syncthing.list"
ct apt-get update -qq
ct apt-get install -y -qq syncthing

echo "==> CT$CTID: syncthing user"
if ! ct id syncthing >/dev/null 2>&1; then
  ct useradd -m -s /usr/sbin/nologin syncthing
fi

echo "==> CT$CTID: output folder $OUT"
ct mkdir -p "$OUT"
# The app writes '.tmp-*' files and renames them when complete; never sync those.
ct bash -c "grep -qxF '.tmp-*' $OUT/.stignore 2>/dev/null || echo '.tmp-*' >> $OUT/.stignore"
ct chown -R syncthing:syncthing "$OUT"
ct chmod 2775 "$OUT"

echo "==> CT$CTID: service (no default ~/Sync folder)"
ct mkdir -p /etc/systemd/system/syncthing@syncthing.service.d
ct bash -c "printf '[Service]\nEnvironment=STNODEFAULTFOLDER=1\n' > /etc/systemd/system/syncthing@syncthing.service.d/override.conf"
ct systemctl daemon-reload
ct systemctl enable --now syncthing@syncthing
sleep 5
ct systemctl is-active syncthing@syncthing

CONFIG=$(ct find /home/syncthing -name config.xml -path '*syncthing*' 2>/dev/null | head -n1 || true)
echo
echo "==> Done"
echo "Syncthing version : $(ct /usr/bin/syncthing --version | head -n1)"
echo "Config            : ${CONFIG:-<not created yet; re-run the find in a few seconds>}"
[ -n "$CONFIG" ] && echo "GUI address       : $(ct grep -oP '(?<=<address>)[^<]+(?=</address>)' "$CONFIG" | head -n1)"
echo "Device ID         : $(ct su -s /bin/sh syncthing -c '/usr/bin/syncthing --device-id 2>/dev/null || /usr/bin/syncthing device-id')"
echo
echo "Put these in the app's .env on CT$CTID:"
echo "APP_UID=$(ct id -u syncthing)"
echo "APP_GID=$(ct id -g syncthing)"
