# Waste Schedule PDF (Home Assistant add-on)

Upload the waste collection PDF, review the generated `waste_collection_schedule` YAML, persist it in the add-on, and serve it read-only so something else (for example Home Assistant) can fetch it.

The add-on knows nothing about Home Assistant's configuration, scripts or restarts. It only produces and serves the file. The "Home Assistant side" section below is a suggestion for your own setup, not something the add-on creates or relies on.

## Security model
- No `map`, no published ports, no `homeassistant_api`/`hassio_api`: the add-on cannot read or write `/config` and has no token.
- The UI (port 8099) is reachable only through ingress. The Dockerfile sets `WASTE_UI_ALLOWED_IPS=172.30.32.2`, so the server accepts only the Supervisor ingress proxy.
- The file server (port 8100) is read-only and serves one path, `GET /sources.yaml`. It has no authentication, so other add-ons on the Supervisor network can read it. The file only contains collection dates and service IDs.

## Files
| File | Purpose |
|---|---|
| `config.yaml` | Add-on definition (ingress, no extra permissions) |
| `Dockerfile` | `python:3.12-alpine` + `poppler-utils` (`pdftotext`) |
| `server.py` | UI server, file server (stdlib only) |
| `static/` | The page: `index.html`, `style.css`, `app.js` |
| `waste_pdf_to_yaml.py` | The parser (same file as the standalone script) |
| `smoke_test.py` | End-to-end test, runs the server itself (not needed on HA) |

Keep `waste_pdf_to_yaml.py` here in sync with the standalone copy when you change it.

## Install (local add-on)
1. Copy `config.yaml`, `Dockerfile`, `server.py`, `waste_pdf_to_yaml.py` and the `static/` folder to `/addons/waste_schedule/` on Home Assistant (Samba "addons" share or SSH). The README and the smoke test are optional.
2. Settings, Add-ons, Add-on store, menu (top right), Check for updates. The add-on shows up under "Local add-ons".
3. Install, Start. Enable "Show in sidebar".
4. Note the hostname on the add-on's Info page. For a local add-on it should be `local-waste-schedule` (slug `waste_schedule`, underscore turned into a hyphen).

## Home Assistant side (suggested, not part of the add-on)
`configuration.yaml`:
```yaml
waste_collection_schedule: !include waste_collection/sources.yaml

shell_command:
  waste_apply_schedule: >
    cp /config/waste_collection/sources.yaml /config/waste_collection/sources.yaml.bak &&
    curl -fsS --max-time 10 -o /config/waste_collection/sources.yaml.new http://local-waste-schedule:8100/sources.yaml &&
    mv /config/waste_collection/sources.yaml.new /config/waste_collection/sources.yaml
```
The very first time there is nothing to back up, so create the folder and an empty file first (`mkdir -p /config/waste_collection && touch /config/waste_collection/sources.yaml`), or the `cp` fails and stops the chain.

`scripts.yaml`:
```yaml
waste_apply_schedule:
  alias: Apply new waste schedule
  sequence:
    - action: shell_command.waste_apply_schedule
    - action: homeassistant.check_config
    - action: persistent_notification.create
      data:
        title: Waste schedule updated
        message: Config copied and checked. Restart Home Assistant to apply.
```
The `shell_command` and script above are examples you would create yourself. The automations (daily reminder, schedule-ending warning) are in the project plan, sections 5 and 6.

## Using the page
The page runs top to bottom:
1. **Upload PDF** and choose options.
2. **Generated YAML**: the parser's notes and the file, with a **Copy** button.
3. **Changes**: a diff against the persisted schedule.
4. **Persist**: stores the YAML in the add-on, replacing the persisted schedule. The previous version is kept.
5. **Persisted schedule**: what is currently stored and served at `/sources.yaml` on port 8100. View it, copy it, or roll back to the previous version.

With the suggested setup above, getting a new schedule into Home Assistant is: persist here, run your fetch script in Home Assistant, check the configuration, restart.

## Options in the UI
- **Recurrences end at:** passes `--until` to the parser. Leave empty to end each recurrence at the PDF's last date (recommended).
- **Leave out empty categories:** passes `--omit-empty`. Default keeps empty categories as inert dummy-date sources so entity names and `source_index` stay stable.

## What was tested, and what was not
Tested here (Python 3.13, headless Chromium): upload, preview, diff, persist, stale-token rejection, rollback, bad uploads, the read-only file server, and the ingress-address check, using the real PDF.

Not tested, because it needs a real Home Assistant:
- Building the image on the Supervisor and starting it as a local add-on.
- Ingress in the real sidebar (the page uses relative URLs, so it should work under the ingress path).
- That `curl` from the Core container reaches `local-waste-schedule:8100`.
- That the ingress address is `172.30.32.2` on your install. If the UI shows "Forbidden", change `WASTE_UI_ALLOWED_IPS` in the Dockerfile (or remove it).

## Running it on your own machine
No flags or special mode. The defaults suit a developer machine; the Dockerfile overrides them inside the add-on.

```
python3 server.py
```
Open http://127.0.0.1:8099/. Needs `pdftotext` (`brew install poppler`) or `pypdf` (`pip install pypdf`) for the parser.

| Setting | Option | Environment | Default | Add-on (Dockerfile) |
|---|---|---|---|---|
| State directory | `--data-dir` | `WASTE_DATA_DIR` | per-user data dir (`~/Library/Application Support/waste-schedule` on macOS, `$XDG_DATA_HOME` or `~/.local/share` on Linux, `%APPDATA%` on Windows) | `/data` |
| Listen address | `--host` | `WASTE_HOST` | `127.0.0.1` | `0.0.0.0` |
| UI port | `--ui-port` | `WASTE_UI_PORT` | `8099` | `8099` |
| File server port | `--file-port` | `WASTE_FILE_PORT` | `8100` | `8100` |
| Allowed UI clients | `--allow` | `WASTE_UI_ALLOWED_IPS` | any (the listen address is the restriction) | `172.30.32.2` |

The server prints the data directory and URLs when it starts. `python3 server.py --help` lists the options.

## Smoke test
```
python3 smoke_test.py path/to/schedule.pdf
```
Starts its own server on free ports with a temporary data dir, runs upload, preview, persist, the file server, rollback and the failure cases, and exits non-zero on any failure. It does not touch your real state directory.
