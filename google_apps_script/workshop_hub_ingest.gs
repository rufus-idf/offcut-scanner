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
  offcut_events: [
    'event_id',
    'offcut_id',
    'event_type',
    'event_at_utc',
    'job_id',
    'user',
    'payload_json',
  ],
  offcut_previews: [
    'preview_ref',
    'offcut_id',
    'svg_path_data',
    'scale_hint',
    'updated_at_utc',
  ],
};

function doGet(e) {
  const action = e && e.parameter && e.parameter.action ? String(e.parameter.action) : '';
  if (action === 'materials') {
    return jsonResponse_({
      ok: true,
      spreadsheet_name: SpreadsheetApp.openById(SHEET_ID).getName(),
      materials: getTextureLibraryMaterials_(),
      received_at_utc: new Date().toISOString(),
    });
  }

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
    const eventRows = payload.sheet_tabs && payload.sheet_tabs.offcut_events ? payload.sheet_tabs.offcut_events : [];
    const previewRows = payload.sheet_tabs && payload.sheet_tabs.offcut_previews ? payload.sheet_tabs.offcut_previews : [];

    appendRows_(spreadsheet, 'offcut_inventory', TAB_CONFIG.offcut_inventory, inventoryRows);
    appendRows_(spreadsheet, 'offcut_shapes', TAB_CONFIG.offcut_shapes, shapeRows);
    appendRows_(spreadsheet, 'offcut_events', TAB_CONFIG.offcut_events, eventRows);
    appendRows_(spreadsheet, 'offcut_previews', TAB_CONFIG.offcut_previews, previewRows);

    return jsonResponse_({
      ok: true,
      spreadsheet_name: spreadsheet.getName(),
      inventory_rows_written: inventoryRows.length,
      shape_rows_written: shapeRows.length,
      event_rows_written: eventRows.length,
      preview_rows_written: previewRows.length,
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
  const sheetHeaders = ensureHeaders_(sheet, headers);

  const values = rows.map((row) => sheetHeaders.map((header) => normalizeCell_(row[header])));
  sheet.getRange(sheet.getLastRow() + 1, 1, values.length, sheetHeaders.length).setValues(values);
}

function ensureHeaders_(sheet, headers) {
  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    return headers;
  }

  const existingWidth = Math.max(sheet.getLastColumn(), headers.length);
  const existing = sheet.getRange(1, 1, 1, existingWidth).getValues()[0];
  const normalizedExisting = existing.map((header) => String(header || '').trim());

  const missingHeaders = headers.filter((header) => !normalizedExisting.includes(header));
  if (missingHeaders.length > 0) {
    const startColumn = normalizedExisting.length + 1;
    sheet.getRange(1, startColumn, 1, missingHeaders.length).setValues([missingHeaders]);
    normalizedExisting.push(...missingHeaders);
  }

  return normalizedExisting;
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

function getTextureLibraryMaterials_() {
  const spreadsheet = SpreadsheetApp.openById(SHEET_ID);
  const sheet = spreadsheet.getSheetByName('texture_library');
  if (!sheet || sheet.getLastRow() < 2) {
    return [];
  }

  const values = sheet.getRange(2, 1, sheet.getLastRow() - 1, 1).getValues();
  const unique = [];
  const seen = new Set();

  values.forEach((row) => {
    const material = String(row[0] || '').trim();
    if (!material || seen.has(material)) {
      return;
    }
    seen.add(material);
    unique.push(material);
  });

  return unique;
}
