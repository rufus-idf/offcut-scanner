# Workshop Hub Google Sheet push setup

## What the app now prepares

The desktop app now builds a Workshop Hub export bundle containing:

- one `offcut_inventory` row
- one `offcut_shapes` row
- the raw scan payload for audit/debug

The export preview inside the app shows exactly what will be saved locally and optionally posted to Google.

## What you need to set up on Google first

The simplest setup is a Google Apps Script web app attached to your Google account.

Why this route:

- the desktop scanner app avoids user OAuth flows on every workshop PC
- the scanner can `POST` plain JSON to a single URL
- Apps Script can append rows into your existing spreadsheet tabs

## Files included in this repo

- `google_apps_script/workshop_hub_ingest.gs` - starter Apps Script for accepting scanner POSTs and appending rows into:
  - `offcut_inventory`
  - `offcut_shapes`

## Recommended setup steps

1. Open the target spreadsheet.
2. Open **Extensions -> Apps Script**.
3. Create a new Apps Script project.
4. Paste in the contents of `google_apps_script/workshop_hub_ingest.gs`.
5. Confirm the `SHEET_ID` matches your spreadsheet ID.
6. Save the script.
7. Deploy it as a **Web app**.
8. Set the web app to execute as **you**.
9. Give access to the smallest audience that still works for your workshop deployment.
10. Copy the deployed `/exec` URL.
11. Open that `/exec` URL once in your browser. You should now get a small JSON health response instead of a `doGet` error.
12. If your scanner build uses a hardcoded push target, update that constant in the app code when the deployment URL changes.
13. In the scanner app, tick **Push to Google Sheet on save**.

Important:

- use the actual web-app `/exec` deployment URL for POST requests
- do **not** use the long `script.googleusercontent.com/...` browser echo URL as the scanner app target

## What gets written where

### offcut_inventory

The app writes:

- `offcut_id`
- `status = IN_STOCK`
- `material`
- `thickness_mm`
- `shape_type`
- `area_mm2`
- `bbox_w_mm`
- `bbox_h_mm`
- `qty`
- plus the recommended metadata fields currently exposed in the UI

### offcut_shapes

The app writes:

- `shape_ref`
- `offcut_id`
- `coord_unit = mm`
- `bbox_x_mm`
- `bbox_y_mm`
- `vertices_json`
- `holes_json = []`
- `version = 1`

## Local save behaviour

Even if push is disabled, the app still saves:

- preview PNG
- mask PNG
- raw scan JSON
- Workshop Hub bundle JSON

That means you can validate the export structure locally before turning on the live sheet push.

## Important note about the sheet itself

I have the sheet URL, but I cannot rely on being able to inspect the live sheet contents or permissions from inside this environment. So you should still verify:

- tab names are exactly `offcut_inventory` and `offcut_shapes`
- row 1 headers match the schema you want
- your Google account has edit permission

## If you want full production hardening later

The next upgrade after this should be:

- dedup / idempotency checks
- retry queue if network push fails
- optional event/history tab writes
- texture/material validation against `texture_library`
