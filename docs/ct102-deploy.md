# Deploying on CT102, step by step

The app runs in Docker as a small web app (plus the original command line).
Syncthing runs natively on CT102 (apt + systemd) as the `syncthing` user. The
only thing they share is `/srv/yt-transcripts`. The app's own database lives in
`/srv/yt-transcripts-data`, which is not synced.

```
LAN    browser ──http://<CT102 LAN IP>:8000 (login)──┐
                                                      ▼
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
`apt-get install -y syncthing=VERSION`, using the exact version string
`madison` printed.

## 2. Create the user and the output folder

```bash
pct exec 102 -- useradd -m -s /usr/sbin/nologin syncthing
pct exec 102 -- id syncthing                    # note the uid and gid
pct exec 102 -- mkdir -p /srv/yt-transcripts
pct exec 102 -- bash -c "echo '.tmp-*' > /srv/yt-transcripts/.stignore"
pct exec 102 -- chown -R syncthing:syncthing /srv/yt-transcripts
pct exec 102 -- chmod 2775 /srv/yt-transcripts
pct exec 102 -- mkdir -p /srv/yt-transcripts-data
pct exec 102 -- chown syncthing:syncthing /srv/yt-transcripts-data
pct exec 102 -- chmod 700 /srv/yt-transcripts-data
```

`.tmp-*` stops Syncthing from picking up notes that the app is still writing.
`/srv/yt-transcripts-data` holds the app's database (schedules, history); it must
stay outside the synced folder.

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

CT102_IP=$(pct exec 102 -- hostname -I | awk '{print $1}')
echo "CT102 LAN IP: $CT102_IP"      # check this is the LAN address, not a docker bridge
pct exec 102 -- sed -i "s/^BIND_ADDRESS=.*/BIND_ADDRESS=$CT102_IP/" /opt/transcript-extractor/.env
```

Then set the password (and, for Search and Schedules, the YouTube API key) in
the file. Open it with:

```bash
pct exec 102 -- nano /opt/transcript-extractor/.env
```

Set `APP_PASSWORD=` to a long password and `YOUTUBE_API_KEY=` to your key, check
`APP_TIMEZONE`, and save. Then check and build:

```bash
pct exec 102 -- cat /opt/transcript-extractor/.env
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose config -q && docker compose build"
```

`docker compose config` refuses to continue if `APP_PASSWORD` is empty.

This is its own compose project, so it doesn't touch the dashboards stack.
It publishes one port, only on `BIND_ADDRESS` (CT102's LAN IP), so it's reachable
from your network but not on CT102's other interfaces. It isn't routed through
Caddy or cloudflared; keep it that way unless you put proper authentication in
front of it.

## 5. Smoke test the app (nothing syncs yet)

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts https://www.youtube.com/watch?v=dQw4w9WgXcQ"
pct exec 102 -- ls -ln /srv/yt-transcripts       # owner must be $ST_UID, not 0
pct exec 102 -- bash -c "head -20 /srv/yt-transcripts/*.md"
pct exec 102 -- bash -c "rm /srv/yt-transcripts/*.md"   # don't send the test note to the vault
```

If the fetch fails with a YouTube error, check the network from CT102 and run
it again. If it says it can't write, the UID/GID in `.env` are wrong.

Then start the web app and check it answers:

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose up -d && sleep 10 && docker compose ps"   # STATUS: healthy
pct exec 102 -- ls -ln /srv/yt-transcripts-data   # transcript-extractor.sqlite3 owned by $ST_UID
```

Open `http://<CT102 LAN IP>:8000` from another machine on your LAN and log in
with `APP_USERNAME` / `APP_PASSWORD`. Don't add any videos yet.

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

## 8. Share with the Mac and the phone

List the devices ZORO is paired with:

```bash
pct exec 100 -- grep -o '<device id="[^"]*" name="[^"]*"' /home/zoro/.local/state/syncthing/config.xml | sort -u
```

Set `MAC_ID` to the Mac's ID from that list, replacing the whole value, then
share the folder with it:

```bash
MAC_ID=XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX-XXXXXXX
pct exec 100 -- su - zoro -c "/usr/bin/syncthing cli config folders yt-transcripts devices add --device-id $MAC_ID"
pct exec 100 -- sed -n '/<folder id="yt-transcripts"/,/<\/folder>/p' /home/zoro/.local/state/syncthing/config.xml | grep 'device id'
```

The last command should list three devices: ZORO, CT102 and the Mac.

Syncthing silently ignores a device ID that isn't in ZORO's device list. The
command still succeeds, but nothing is shared. So only add the phone from
ZORO if the phone appears in the list above.

- **Mac:** the Syncthing GUI (http://127.0.0.1:8384) shows "ZORO wants to
  share folder YouTube Transcripts". Click *Add*, and on the *General* tab set
  the folder path to `/Users/tok/obsidian_vault/tokvault/YouTube Transcripts`
  before saving.
- **Phone:** if the phone is paired with the Mac rather than ZORO, share it
  from the Mac. In the Mac GUI, edit *YouTube Transcripts*, open the *Sharing*
  tab, tick the phone and save. Then accept the folder on the phone into the
  vault's `YouTube Transcripts` folder.

## 9. Verify end to end

```bash
URL='https://www.youtube.com/watch?v=dQw4w9WgXcQ'
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts '$URL'"
sleep 20
pct exec 100 -- ls -la "/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts/"
pct exec 100 -- bash -c 'for c in $(docker ps -q --filter name=hermes); do echo "== $c"; docker exec "$c" ls "/root/vault/YouTube Transcripts/" 2>&1; done'
```

There may be more than one Hermes container. Only the one with Amy's vault
mount lists the note; the others report "No such file or directory".

Then check that the note shows up in Obsidian on the Mac and on the phone.
Remove the test note with
`pct exec 102 -- bash -c "rm /srv/yt-transcripts/*dQw4w9WgXcQ*"`. CT102 is
Send Only, so the deletion reaches every device.
On CT102, `ls -ln /srv/yt-transcripts` should show `$ST_UID` as the owner.

When everything works, remove the snapshot:
`pct delsnapshot 102 pre-yt-transcripts`.

## Day-to-day use

Open `http://<CT102 LAN IP>:8000`:

- **Transcripts:** paste YouTube links; see what's saved, failed or has no captions,
  and retry failures.
- **Search:** find videos by keyword, by channel, or both (needs `YOUTUBE_API_KEY`) and
  save the ones you tick.
- **Schedules:** channels and keyword searches that run automatically and save new videos.
- **Claude routine:** builds the prompt for the summary task (below).

The original command still works, and shows up on the Transcripts page too:

```bash
URL='https://www.youtube.com/watch?v=VIDEO_ID'
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts '$URL'"
```

To update the app:

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && git pull && docker compose up -d --build"
```

## Summaries and email (Claude routine)

New notes have `summary_status: pending` and an empty `## Summary` section. A
scheduled task in the Claude desktop app on the Mac fills them in:

1. In the web app, open **Claude routine**. Enter the vault path on the Mac
   (`~/obsidian_vault/tokvault`) and your email, and copy the prompt.
2. In the Claude desktop app, create a scheduled task with that prompt, give it
   access to the vault folder, and connect Gmail if you want the digest email.
3. Run it once by hand. Summarised notes switch to `summary_status: done`.

The routine edits the notes on the Mac. Those edits sync to ZORO and the phone
as usual; CT102 is Send Only, so its copies stay unsummarised, which is harmless
because the app never rewrites a note that already exists.

## Upgrading from the command-line-only version

If CT102 already runs the earlier version (no web app):

```bash
pct exec 102 -- mkdir -p /srv/yt-transcripts-data
pct exec 102 -- chown syncthing:syncthing /srv/yt-transcripts-data
pct exec 102 -- chmod 700 /srv/yt-transcripts-data
pct exec 102 -- bash -c "cd /opt/transcript-extractor && git pull"
pct exec 102 -- bash -c "cd /opt/transcript-extractor && grep -q '^DATA_DIR=' .env || sed -n '/^DATA_DIR=/,\$p' .env.example >> .env"
```

The last line appends the new settings from `.env.example` to your `.env`
without touching `APP_UID`, `APP_GID` or `OUTPUT_DIR`. Then set `BIND_ADDRESS`,
`APP_PASSWORD` and `YOUTUBE_API_KEY` as in step 4, and start it:

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose config -q && docker compose up -d --build"
```

Existing notes are recognised by the video ID in their file name, so they're
never fetched or overwritten again.

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
  fetch that video again, since its note still exists on CT102 and it's in
  the app's history.
- Transcripts are untrusted third-party text in a vault that Amy can write
  to. Every note has `type: youtube-transcript` in its frontmatter, so it
  can be recognised as external content. The Claude routine prompt tells
  Claude to treat transcripts as material to summarise and never follow
  instructions inside them.

`deploy/ct102/setup-syncthing.sh` does steps 1–3 in one go if you'd rather
run a script.
