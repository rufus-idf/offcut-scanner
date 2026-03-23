const SHEET_ID = '1-qS6gWekGtEhjczboAyAShJHHamK0ZuVlR7CFbubxxo';

const TAB_CONFIG = {
  offcut_inventory: [
    'offcut_id',
    'status',
    'material',
    'thickness_mm',
    'shape_type',
    'area_mm2',
    'bbox_w_mm',
    'bbox_h_mm',
    'qty',
    'grade',
    'sheet_origin_job',
    'sheet_origin_index',
    'captured_at_utc',
    'min_internal_width_mm',
    'usable_score',
    'location',
    'preview_ref',
    'shape_ref',
    'notes',
  ],
  offcut_shapes: [
    'shape_ref',
    'offcut_id',
    'coord_unit',
    'bbox_x_mm',
    'bbox_y_mm',
    'vertices_json',
    'holes_json',
    'version',
  ],
};

function doGet() {
  return jsonResponse_({
    ok: true,
    message: 'Workshop Hub ingest endpoint is running.',
    expected_method: 'POST',
    received_at_utc: new Date().toISOString(),
  });
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents);
    const spreadsheet = SpreadsheetApp.openById(SHEET_ID);

    const inventoryRows = payload.sheet_tabs && payload.sheet_tabs.offcut_inventory ? payload.sheet_tabs.offcut_inventory : [];
    const shapeRows = payload.sheet_tabs && payload.sheet_tabs.offcut_shapes ? payload.sheet_tabs.offcut_shapes : [];

    appendRows_(spreadsheet, 'offcut_inventory', TAB_CONFIG.offcut_inventory, inventoryRows);
    appendRows_(spreadsheet, 'offcut_shapes', TAB_CONFIG.offcut_shapes, shapeRows);

    return jsonResponse_({
      ok: true,
      inventory_rows_written: inventoryRows.length,
      shape_rows_written: shapeRows.length,
      received_at_utc: new Date().toISOString(),
    });
  } catch (error) {
    return jsonResponse_({
      ok: false,
      error: String(error),
    });
  }
}

function appendRows_(spreadsheet, tabName, headers, rows) {
  if (!rows || rows.length === 0) {
    return;
  }

  const sheet = spreadsheet.getSheetByName(tabName) || spreadsheet.insertSheet(tabName);
  ensureHeaders_(sheet, headers);

  const values = rows.map((row) => headers.map((header) => normalizeCell_(row[header])));
  sheet.getRange(sheet.getLastRow() + 1, 1, values.length, headers.length).setValues(values);
}

function ensureHeaders_(sheet, headers) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    return;
  }

  const existing = sheet.getRange(1, 1, 1, headers.length).getValues()[0];
  const mismatch = headers.some((header, index) => existing[index] !== header);
  if (mismatch) {
    throw new Error(`Header mismatch in tab ${sheet.getName()}.`);
  }
}

function normalizeCell_(value) {
  if (value === null || value === undefined) {
    return '';
  }
  return value;
}

function jsonResponse_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}