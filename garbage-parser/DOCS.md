# Waste Schedule PDF

Upload the waste collection PDF, review the generated YAML, and persist it. The add-on then serves the YAML read-only so Home Assistant can fetch it.

## Using it

Start the add-on and click **Open Web UI** on its Info tab.

1. **Upload PDF.** Two options:
   - *Recurrences end at*: end date for recurring schedules. Empty means each recurrence ends at the last date in the PDF, which is what you want unless you know better.
   - *Leave out empty categories*: see "Empty categories" below.
2. **Generated YAML** and **Changes** show the result and how it differs from what is persisted. Use the copy button to copy the YAML.
3. **Persist** stores it in the add-on and keeps the previous version.

The **Persisted schedule** card at the bottom shows what is currently stored. Expand it to read or copy the file, or to roll back to the previous version.

## Getting the file into Home Assistant

The generated YAML goes under `waste_collection_schedule:` in your Home Assistant configuration.

Restart Home Assistant afterwards. Changes to this integration need a restart.

The persisted YAML is also served read-only at `http://<add-on hostname>:8100/sources.yaml` (the hostname is on the add-on's Info page), in case you want to build your own automations around it.

## What is generated

- One `static` source per category, named in English.
- A category with three or more evenly spaced dates becomes a weekly recurrence (`frequency: WEEKLY`, `interval`, `start`, `until`). Anything else, and Garden Waste always, becomes a plain list of dates.
- One sensor per category.
- `waste_next`: all categories combined. Collections on the same day are merged into one entry.
- `waste_schedule_end`: its `daysTo` attribute counts days to the last collection date in the PDF. Use it to warn yourself before the schedule runs out.

### Empty categories

A category that is missing from the PDF, or listed without dates, is kept as a source with a single dummy date earlier this year (marked `# dummy date`). Its sensor still exists and shows nothing upcoming, and the order of the sources never changes. With *Leave out empty categories* they are left out instead, which removes their sensors.

## Notes

- The add-on does not start on boot by default. Start it when a new PDF arrives. The file on port 8100 is only available while it is running.
- It has no access to `/config`, no Home Assistant token, and publishes no ports. The web UI is only reachable through Home Assistant.
- Port 8100 is read-only and has no authentication, so other add-ons on the same network can read the file. It only contains collection dates and service IDs.
- Services the parser does not recognise stop the import with an error naming the service, so a new category is never silently dropped.
