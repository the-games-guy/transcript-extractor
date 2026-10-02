# Deploying on CT102, step by step

The app runs in Docker. Syncthing runs natively on CT102 (apt + systemd)
as the `syncthing` user. The only thing they share is `/srv/yt-transcripts`.

```
CT102  docker: yt-transcripts ──writes as syncthing's UID──> /srv/yt-transcripts
       native: syncthing@syncthing ── folder "yt-transcripts", Send Only ──┐
                                                                            ▼
ZORO   .../_vault/tokvault/YouTube Transcripts  (Send & Receive) ──> Mac + phone
```

Everything runs from **one shell on the Proxmox host** unless it's marked
**Mac** or **Phone**. Later steps reuse variables set in earlier ones, so keep
the same shell open throughout. Pairing is done with `syncthing cli`, so
CT102's Syncthing GUI never has to listen on the LAN.

## 0. Safety net

```bash
pct snapshot 102 pre-yt-transcripts
pct exec 100 -- cp /home/zoro/.local/state/syncthing/config.xml /home/zoro/.local/state/syncthing/config.xml.bak-yt
```

If `pct snapshot` fails because CT102's storage doesn't support snapshots,
use `vzdump 102 --mode snapshot` instead, or skip this step.

## 1. Install Syncthing on CT102

```bash
pct exec 102 -- apt-get update
pct exec 102 -- apt-get install -y curl gnupg ca-certificates git
pct exec 102 -- bash -c "curl -fsSL https://syncthing.net/release-key.gpg | gpg --dearmor -o /usr/share/keyrings/syncthing-archive-keyring.gpg"
pct exec 102 -- bash -c "echo 'deb [signed-by=/usr/share/keyrings/syncthing-archive-keyring.gpg] https://apt.syncthing.net/ syncthing stable' > /etc/apt/sources.list.d/syncthing.list"
pct exec 102 -- apt-get update
pct exec 102 -- apt-cache madison syncthing     # see which versions are available
pct exec 102 -- apt-get install -y syncthing
pct exec 102 -- /usr/bin/syncthing --version
```

2.x and ZORO's 1.29.2 can sync with each other. If `madison` lists a 1.29
build and you'd rather match ZORO, install it with
`apt-get install -y syncthing=<that version>`.

## 2. Create the user and the output folder

```bash
pct exec 102 -- useradd -m -s /usr/sbin/nologin syncthing
pct exec 102 -- id syncthing                    # note the uid and gid
pct exec 102 -- mkdir -p /srv/yt-transcripts
pct exec 102 -- bash -c "echo '.tmp-*' > /srv/yt-transcripts/.stignore"
pct exec 102 -- chown -R syncthing:syncthing /srv/yt-transcripts
pct exec 102 -- chmod 2775 /srv/yt-transcripts
```

`.tmp-*` stops Syncthing from picking up notes that the app is still writing.

## 3. Start Syncthing on CT102

```bash
pct exec 102 -- mkdir -p /etc/systemd/system/syncthing@syncthing.service.d
pct exec 102 -- bash -c "printf '[Service]\nEnvironment=STNODEFAULTFOLDER=1\n' > /etc/systemd/system/syncthing@syncthing.service.d/override.conf"
pct exec 102 -- systemctl daemon-reload
pct exec 102 -- systemctl enable --now syncthing@syncthing
sleep 10
pct exec 102 -- systemctl is-active syncthing@syncthing      # expect: active
pct exec 102 -- ss -ltn | grep 8384                          # expect: 127.0.0.1:8384 only
```

`STNODEFAULTFOLDER=1` stops Syncthing from creating an unwanted `~/Sync` folder.

## 4. Deploy the app

```bash
pct exec 102 -- git clone https://github.com/the-games-guy/transcript-extractor.git /opt/transcript-extractor
pct exec 102 -- cp /opt/transcript-extractor/.env.example /opt/transcript-extractor/.env

ST_UID=$(pct exec 102 -- id -u syncthing); ST_GID=$(pct exec 102 -- id -g syncthing)
echo "uid=$ST_UID gid=$ST_GID"
pct exec 102 -- sed -i "s/^APP_UID=.*/APP_UID=$ST_UID/; s/^APP_GID=.*/APP_GID=$ST_GID/" /opt/transcript-extractor/.env
pct exec 102 -- cat /opt/transcript-extractor/.env

pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose config -q && docker compose build"
```

This is its own compose project, so it doesn't touch the dashboards stack.
It has no ports and isn't routed through Caddy or cloudflared.

## 5. Smoke test the app (nothing syncs yet)

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts https://www.youtube.com/watch?v=dQw4w9WgXcQ"
pct exec 102 -- ls -ln /srv/yt-transcripts       # owner must be $ST_UID, not 0
pct exec 102 -- bash -c "head -20 /srv/yt-transcripts/*.md"
pct exec 102 -- bash -c "rm /srv/yt-transcripts/*.md"   # don't send the test note to the vault
```

If the fetch fails with a YouTube error, check the network from CT102 and run
it again. If it says it can't write, the UID/GID in `.env` are wrong.

## 6. Ignore rules, before the folder exists anywhere

Each vault's main Syncthing folder has to ignore the nested folder, or two
Syncthing folders will manage the same files. `.stignore` doesn't sync, so
add the rule on every device.

```bash
# ZORO
pct exec 100 -- su - zoro -c "printf '\n/YouTube Transcripts\n' >> ~/.hermes/profiles/assistant/skills/_vault/tokvault/.stignore"
pct exec 100 -- su - zoro -c "cat ~/.hermes/profiles/assistant/skills/_vault/tokvault/.stignore"
```

**Mac** (Terminal):
```bash
printf '\n/YouTube Transcripts\n' >> ~/obsidian_vault/tokvault/.stignore
cat ~/obsidian_vault/tokvault/.stignore
```

**Phone:** in the Syncthing app, open the vault folder, go to *Ignore
patterns*, and add `/YouTube Transcripts`.

## 7. Pair CT102 and ZORO

```bash
CT102_ID=$(pct exec 102 -- su -s /bin/sh syncthing -c '/usr/bin/syncthing --device-id 2>/dev/null || /usr/bin/syncthing device-id')
ZORO_ID=$(pct exec 100 -- su - zoro -c '/usr/bin/syncthing --device-id')
echo "CT102=$CT102_ID"; echo "ZORO=$ZORO_ID"     # both should look like XXXXXXX-XXXXXXX-... (8 groups)
```

On **CT102**, add ZORO, then add the folder as Send Only and share it with ZORO:

```bash
pct exec 102 -- su -s /bin/sh syncthing -c "/usr/bin/syncthing cli config devices add --device-id $ZORO_ID --name zoro"
pct exec 102 -- su -s /bin/sh syncthing -c "/usr/bin/syncthing cli config folders add --id yt-transcripts --label 'YouTube Transcripts' --path /srv/yt-transcripts --type sendonly"
pct exec 102 -- su -s /bin/sh syncthing -c "/usr/bin/syncthing cli config folders yt-transcripts devices add --device-id $ZORO_ID"
```

On **ZORO**, add CT102, then add the folder at the exact vault path and share
it with CT102:

```bash
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config devices add --device-id $CT102_ID --name ct102"
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders add --id yt-transcripts --label 'YouTube Transcripts' --path '/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts' --type sendreceive"
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders yt-transcripts devices add --device-id $CT102_ID"
```

Check both sides:

```bash
pct exec 102 -- grep 'folder id="yt-transcripts"' /home/syncthing/.local/state/syncthing/config.xml   # type="sendonly"
pct exec 100 -- grep 'folder id="yt-transcripts"' /home/zoro/.local/state/syncthing/config.xml      # type="sendreceive"
pct exec 100 -- ls -la "/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts/"   # has .stfolder, no extra nested folder
```

## 8. Share from ZORO to the Mac and the phone

List the devices ZORO already knows about, then add the Mac and phone to the
new folder:

```bash
pct exec 100 -- grep -o '<device id="[^"]*" name="[^"]*"' /home/zoro/.local/state/syncthing/config.xml | sort -u
MAC_ID=<paste the Mac's id>
PHONE_ID=<paste the phone's id>
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders yt-transcripts devices add --device-id $MAC_ID"
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders yt-transcripts devices add --device-id $PHONE_ID"
```

- **Mac:** the Syncthing GUI (http://127.0.0.1:8384) shows "ZORO wants to
  share folder YouTube Transcripts". Click *Add*, and on the *General* tab set
  the folder path to `/Users/tok/obsidian_vault/tokvault/YouTube Transcripts`
  before saving.
- **Phone:** accept the folder and point it at the `YouTube Transcripts`
  folder inside the vault.

## 9. Verify end to end

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts <a real video url>"
sleep 20
pct exec 100 -- ls -la "/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts/"
pct exec 100 -- docker ps --filter name=hermes                                  # copy the sandbox id
pct exec 100 -- docker exec <id> ls "/root/vault/YouTube Transcripts/"
```

Then check that the note shows up in Obsidian on the Mac and on the phone.
On CT102, `ls -ln /srv/yt-transcripts` should show `$ST_UID` as the owner.

When everything works, remove the snapshot:
`pct delsnapshot 102 pre-yt-transcripts`.

## Day-to-day use

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts <url> [<url> ...]"
```

To update the app:

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && git pull && docker compose build"
```

Notes live in `/srv/yt-transcripts`, outside the app folder, so updates and
rebuilds never touch them.

## Rolling back

```bash
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders yt-transcripts delete"   # stops syncing; files stay
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config devices $CT102_ID delete"
pct rollback 102 pre-yt-transcripts                                                       # CT102 back to before step 1
```

Remove the folder on the Mac and the phone in their Syncthing apps.

## Known behaviour

- Deleting a note in Obsidian doesn't delete CT102's copy, because CT102 is
  Send Only. CT102 shows "Out of Sync", which is harmless. The app won't
  fetch that video again, since its note still exists on CT102.
- Transcripts are untrusted third-party text in a vault that Amy can write
  to. Every note has `type: youtube-transcript` in its frontmatter, so it
  can be recognised as external content.

`deploy/ct102/setup-syncthing.sh` does steps 1–3 in one go if you'd rather
run a script.
