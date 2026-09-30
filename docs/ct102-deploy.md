# Deploying on CT102

The app runs in Docker. Syncthing runs natively on CT102 (apt + systemd)
as the `syncthing` user. The only thing they share is `/srv/yt-transcripts`.

```
CT102  docker: yt-transcripts ──writes as syncthing's UID──> /srv/yt-transcripts
       native: syncthing@syncthing ── folder "yt-transcripts", Send Only ──┐
                                                                            ▼
ZORO   .../_vault/tokvault/YouTube Transcripts  (Send & Receive) ──> Mac + phone
```

All commands run from the Proxmox host shell.

## 1. Install Syncthing on CT102

Copy `deploy/ct102/setup-syncthing.sh` to the Proxmox host and run:

```bash
bash setup-syncthing.sh 102
```

The script is safe to re-run. It:

- adds the Syncthing apt repo and installs the package
- creates the `syncthing` user (no login shell)
- creates `/srv/yt-transcripts`, owned by `syncthing`, with a `.stignore`
  containing `.tmp-*` so partially written notes never sync
- starts `syncthing@syncthing` at boot, without the default `~/Sync` folder
- prints the Syncthing version, config path, GUI address, device ID and the
  `APP_UID` / `APP_GID` values for step 2

Check two things in the output:

- **GUI address** should be `127.0.0.1:8384`, which is the default.
- **Version.** ZORO runs 1.29.2. If CT102 installed 2.x, the two can still
  sync with each other, but the CLI and config differ a little. To keep
  both on 1.x, pin the package, for example
  `pct exec 102 -- apt-get install syncthing=1.29.*`.

## 2. Deploy the app

```bash
pct exec 102 -- git clone https://github.com/the-games-guy/transcript-extractor.git /opt/transcript-extractor
pct exec 102 -- bash -c "cd /opt/transcript-extractor && cp .env.example .env"
```

(If the repo is private, clone it with a deploy key or token.)

Set `APP_UID` and `APP_GID` in `/opt/transcript-extractor/.env` to the
values the script printed. **Don't assume 1000.** If they're wrong, the app
either can't write (it stops with an error naming the UID) or creates files
that Syncthing can't manage.

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose config -q && docker compose build"
```

This is a separate compose project from the dashboards stack, so the
finance, health and other services aren't touched. The container has no
published ports and isn't behind Caddy or cloudflared. Its filesystem is
read-only, it has no Linux capabilities, and the only thing it can write to
is `/output`.

Smoke test, before pairing:

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts https://www.youtube.com/watch?v=dQw4w9WgXcQ"
pct exec 102 -- ls -ln /srv/yt-transcripts    # owner must be syncthing's UID/GID
```

Delete that test note before step 4 if you don't want it in the vault.

## 3. Ignore rules, before accepting the folder anywhere

The main vault folder on each device has to ignore the nested folder,
otherwise two Syncthing folders manage the same files. `.stignore` doesn't
sync, so add it on every device.

```bash
# ZORO
pct exec 100 -- su - zoro -c "echo '/YouTube Transcripts' >> ~/.hermes/profiles/assistant/skills/_vault/tokvault/.stignore"
```

- Mac: add `/YouTube Transcripts` to `/Users/tok/obsidian_vault/tokvault/.stignore`.
- Phone: add the same pattern to the vault folder's ignore list in the Syncthing app.

## 4. Pair and share

Open the CT102 GUI temporarily, the same way as on ZORO. Replace
`CT102_LAN_IP` with CT102's address:

```bash
CONFIG=$(pct exec 102 -- find /home/syncthing -name config.xml)
pct exec 102 -- sed -i 's#<address>127.0.0.1:8384</address>#<address>CT102_LAN_IP:8384</address>#' "$CONFIG"
pct exec 102 -- systemctl restart syncthing@syncthing
```

- **CT102:** add ZORO as a device. Add a folder with path `/srv/yt-transcripts`,
  ID `yt-transcripts`, type **Send Only**, and share it with ZORO. Leave
  the folder's "Ignore Permissions" setting off.
- **ZORO:** accept the device and the folder, typing the path in full:
  `/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts`.
  Check with `ls` that no extra nested folder was created.
- **ZORO:** share `yt-transcripts` with the Mac and the phone.
- **Mac:** accept at `/Users/tok/obsidian_vault/tokvault/YouTube Transcripts`.
- **CT102:** set the GUI address back to `127.0.0.1:8384` and restart the
  service. Then check it:
  `pct exec 102 -- ss -ltnp | grep 8384` should show `127.0.0.1` only.

## 5. Verify end to end

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && docker compose run --rm yt-transcripts <some-video-url>"
pct exec 100 -- ls -la "/home/zoro/.hermes/profiles/assistant/skills/_vault/tokvault/YouTube Transcripts/"
pct exec 100 -- docker ps --filter name=hermes
pct exec 100 -- docker exec <id> ls "/root/vault/YouTube Transcripts/"
```

Confirm the note shows up in Obsidian on the Mac, and that
`ls -ln /srv/yt-transcripts` on CT102 shows syncthing's UID, not 0.

## Updating the app

```bash
pct exec 102 -- bash -c "cd /opt/transcript-extractor && git pull && docker compose build"
```

Notes live in `/srv/yt-transcripts`, outside the app folder, so updates and
rebuilds never touch them.

## Known behaviour

- Deleting a note in Obsidian doesn't delete CT102's copy, because CT102 is
  Send Only. CT102 shows "Out of Sync", which is harmless. The app won't
  fetch that video again, since its note still exists on CT102.
- Transcripts are untrusted third-party text in a vault that Amy can write
  to. Every note has `type: youtube-transcript` in its frontmatter, so it
  can be recognised as external content.
