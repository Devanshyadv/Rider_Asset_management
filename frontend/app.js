// ============================================================
//  Rider Asset Management — Frontend App
// ============================================================
const API_BASE = 'http://localhost:8000';

// ────────────────────────────────────────────────────────────
//  API fetch wrapper
// ────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const cfg = { headers: { 'Content-Type': 'application/json' }, ...options };
  if (cfg.body && typeof cfg.body === 'object') cfg.body = JSON.stringify(cfg.body);
  const res = await fetch(url, cfg);
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { detail: text }; }
  if (!res.ok) {
    const msg = data?.detail || `HTTP ${res.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

// ────────────────────────────────────────────────────────────
//  Toast notifications
// ────────────────────────────────────────────────────────────
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  const icons = { success: '✓', error: '✕', warning: '⚠' };
  toast.innerHTML = `<span class="toast-icon">${icons[type] || '✓'}</span><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(120%)';
    setTimeout(() => toast.remove(), 400);
  }, 3500);
}

// ────────────────────────────────────────────────────────────
//  Modal
// ────────────────────────────────────────────────────────────
let _modalCallback = null;

function openModal(title, bodyHtml, confirmLabel = 'Confirm', onConfirm = null, danger = false) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  const confirmBtn = document.getElementById('modal-confirm');
  const cancelBtn = document.getElementById('modal-cancel');
  confirmBtn.textContent = confirmLabel;
  confirmBtn.className = `btn ${danger ? 'btn-danger' : 'btn-primary'}`;
  confirmBtn.style.display = onConfirm ? '' : 'none';
  cancelBtn.style.display = '';
  _modalCallback = onConfirm;
  document.getElementById('modal-overlay').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
  _modalCallback = null;
}

document.getElementById('modal-confirm').addEventListener('click', () => {
  if (_modalCallback) _modalCallback();
  closeModal();
});
document.getElementById('modal-cancel').addEventListener('click', closeModal);
document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

// ────────────────────────────────────────────────────────────
//  Navigation
// ────────────────────────────────────────────────────────────
const sectionLoaders = {
  dashboard: loadDashboard,
  assets: loadAssets,
  issuances: () => {},
  'bulk-upload': setupBulkUpload,
  'rider-lookup': () => {},
  settlement: setupSettlement,
  vendors: () => {},
  reports: () => {},
  admin: () => {},
};

function activateSection(id) {
  document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
  const navItem = document.querySelector(`.nav-item[data-section="${id}"]`);
  if (navItem) navItem.classList.add('active');
  const section = document.getElementById(`section-${id}`);
  if (section) section.classList.add('active');
  (sectionLoaders[id] || (() => {}))();
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => activateSection(item.dataset.section));
});

// Tab switching — uses .tab buttons and .tab-panel panels
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    const section = btn.closest('.section');
    section.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const target = btn.dataset.tab;
    section.querySelectorAll('.tab-panel').forEach(p => {
      p.classList.toggle('active', p.id === `tab-${target}`);
    });
  });
});

// ────────────────────────────────────────────────────────────
//  Helpers
// ────────────────────────────────────────────────────────────
function statusBadge(status) {
  const map = {
    active: 'badge-success', completed: 'badge-success',
    inactive: 'badge-danger', pending: 'badge-warning',
    partial: 'badge-warning', waived: 'badge-warning',
    replaced: 'badge-warning', written_off: 'badge-danger',
    vendor_passed: 'badge-warning', deducted: 'badge-success',
    skipped: 'badge-danger',
  };
  const cls = map[(status || '').toLowerCase()] || 'badge-default';
  return `<span class="badge ${cls}">${status || '-'}</span>`;
}

function fmtCurrency(val) {
  if (val == null) return '-';
  return '₹' + Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2 });
}

function fmtDate(val) {
  if (!val) return '-';
  return new Date(val).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function setResult(id, html, hidden = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = html;
  if (hidden) el.classList.add('hidden'); else el.classList.remove('hidden');
}

function resultPre(data) {
  return `<pre style="font-size:12px;background:var(--bg);padding:1rem;border-radius:6px;overflow:auto;max-height:300px;white-space:pre-wrap">${JSON.stringify(data, null, 2)}</pre>`;
}

// ────────────────────────────────────────────────────────────
//  DASHBOARD
// ────────────────────────────────────────────────────────────
async function loadDashboard() {
  const statCards = document.getElementById('stat-cards');
  statCards.innerHTML = '<div class="stat-card loading">Loading…</div>';

  try {
    const assets = await apiFetch('/api/assets');
    const active = assets.filter(a => a.status === 'active').length;
    const inactive = assets.length - active;
    statCards.innerHTML = `
      <div class="stat-card">
        <div class="stat-label">Total Assets</div>
        <div class="stat-value">${assets.length}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Active Assets</div>
        <div class="stat-value" style="color:var(--success)">${active}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Inactive Assets</div>
        <div class="stat-value" style="color:var(--danger)">${inactive}</div>
      </div>`;
  } catch (e) {
    statCards.innerHTML = `<div class="stat-card"><span style="color:var(--danger)">Could not load stats: ${e.message}</span></div>`;
  }
}

// ────────────────────────────────────────────────────────────
//  ASSETS
// ────────────────────────────────────────────────────────────
async function loadAssets(statusFilter) {
  const qs = statusFilter ? `?status=${statusFilter}` : '';
  try {
    const data = await apiFetch(`/api/assets${qs}`);
    const tbody = document.getElementById('assets-tbody');
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No assets found.</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(a => `
      <tr>
        <td>${a.name}</td>
        <td>${fmtCurrency(a.costPrice)}</td>
        <td>${a.absorptionType}</td>
        <td>${a.vendorId || '-'}</td>
        <td>${a.stock ?? 0}</td>
        <td>${statusBadge(a.status)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" onclick="openEditAssetModal('${a._id}')">Edit</button>
          <button class="btn btn-danger btn-sm" onclick="deleteAsset('${a._id}', '${a.name}')">Delete</button>
        </td>
      </tr>`).join('');
  } catch (e) {
    showToast(e.message, 'error');
  }
}

function openCreateAssetModal() {
  openModal(
    'Create New Asset',
    `<div class="form-grid">
      <div class="form-group">
        <label>Name *</label>
        <input class="input" id="m-asset-name" placeholder="e.g. Delivery Bag" />
      </div>
      <div class="form-group">
        <label>Cost Price (₹) *</label>
        <input class="input" type="number" id="m-asset-cost" min="0.01" step="0.01" placeholder="0.00" />
      </div>
      <div class="form-group">
        <label>Absorption Type *</label>
        <select class="input" id="m-asset-absorption" onchange="toggleVendorField()">
          <option value="VENDOR">VENDOR</option>
          <option value="SELF">SELF</option>
        </select>
      </div>
      <div class="form-group" id="m-vendor-row">
        <label>Vendor ID</label>
        <input class="input" id="m-asset-vendor" placeholder="MongoDB ObjectId" />
      </div>
      <div class="form-group">
        <label>Stock *</label>
        <input class="input" type="number" id="m-asset-stock" value="0" min="0" />
      </div>
    </div>`,
    'Create Asset',
    async () => {
      const name = document.getElementById('m-asset-name').value.trim();
      const costPrice = parseFloat(document.getElementById('m-asset-cost').value);
      const absorptionType = document.getElementById('m-asset-absorption').value;
      const vendorId = document.getElementById('m-asset-vendor')?.value.trim() || undefined;
      const stock = parseInt(document.getElementById('m-asset-stock').value, 10);
      if (!name || isNaN(costPrice) || costPrice <= 0) {
        showToast('Name and valid cost price are required.', 'warning'); return;
      }
      const payload = { name, costPrice, absorptionType, stock: isNaN(stock) ? 0 : stock };
      if (absorptionType === 'VENDOR' && vendorId) payload.vendorId = vendorId;
      try {
        await apiFetch('/api/assets', { method: 'POST', body: payload });
        showToast('Asset created.');
        loadAssets(document.getElementById('asset-status-filter').value || undefined);
      } catch (e) { showToast(e.message, 'error'); }
    }
  );
}

function toggleVendorField() {
  const row = document.getElementById('m-vendor-row');
  if (row) row.style.display = document.getElementById('m-asset-absorption').value === 'VENDOR' ? '' : 'none';
}

async function openEditAssetModal(id) {
  try {
    const assets = await apiFetch(`/api/assets?status=`);
    const asset = assets.find(a => a._id === id);
    if (!asset) { showToast('Asset not found.', 'error'); return; }

    openModal(
      'Edit Asset',
      `<div class="form-grid">
        <div class="form-group">
          <label>Name</label>
          <input class="input" id="m-asset-name" value="${asset.name}" />
        </div>
        <div class="form-group">
          <label>Cost Price (₹)</label>
          <input class="input" type="number" id="m-asset-cost" value="${asset.costPrice}" min="0.01" step="0.01" />
        </div>
        <div class="form-group">
          <label>Absorption Type</label>
          <select class="input" id="m-asset-absorption" onchange="toggleVendorField()">
            <option value="VENDOR" ${asset.absorptionType === 'VENDOR' ? 'selected' : ''}>VENDOR</option>
            <option value="SELF" ${asset.absorptionType === 'SELF' ? 'selected' : ''}>SELF</option>
          </select>
        </div>
        <div class="form-group" id="m-vendor-row" style="${asset.absorptionType !== 'VENDOR' ? 'display:none' : ''}">
          <label>Vendor ID</label>
          <input class="input" id="m-asset-vendor" value="${asset.vendorId || ''}" />
        </div>
        <div class="form-group">
          <label>Stock</label>
          <input class="input" type="number" id="m-asset-stock" value="${asset.stock ?? 0}" min="0" />
        </div>
        <div class="form-group">
          <label>Status</label>
          <select class="input" id="m-asset-status">
            <option value="active" ${asset.status === 'active' ? 'selected' : ''}>active</option>
            <option value="inactive" ${asset.status === 'inactive' ? 'selected' : ''}>inactive</option>
          </select>
        </div>
      </div>`,
      'Save Changes',
      async () => {
        const patch = {};
        const name = document.getElementById('m-asset-name').value.trim();
        const costPrice = parseFloat(document.getElementById('m-asset-cost').value);
        const absorptionType = document.getElementById('m-asset-absorption').value;
        const vendorId = document.getElementById('m-asset-vendor')?.value.trim() || undefined;
        const stock = parseInt(document.getElementById('m-asset-stock').value, 10);
        const status = document.getElementById('m-asset-status').value;
        if (name) patch.name = name;
        if (!isNaN(costPrice) && costPrice > 0) patch.costPrice = costPrice;
        if (absorptionType) patch.absorptionType = absorptionType;
        if (absorptionType === 'VENDOR' && vendorId) patch.vendorId = vendorId;
        if (!isNaN(stock)) patch.stock = stock;
        if (status) patch.status = status;
        try {
          await apiFetch(`/api/assets/${id}`, { method: 'PATCH', body: patch });
          showToast('Asset updated.');
          loadAssets(document.getElementById('asset-status-filter').value || undefined);
        } catch (e) { showToast(e.message, 'error'); }
      }
    );
  } catch (e) { showToast(e.message, 'error'); }
}

function deleteAsset(id, name) {
  openModal(
    'Delete Asset',
    `<p>Deactivate <strong>${name}</strong>? This sets status to <em>inactive</em> (soft delete).</p>`,
    'Deactivate',
    async () => {
      try {
        await apiFetch(`/api/assets/${id}`, { method: 'DELETE' });
        showToast('Asset deactivated.');
        loadAssets(document.getElementById('asset-status-filter').value || undefined);
      } catch (e) { showToast(e.message, 'error'); }
    },
    true
  );
}

// ────────────────────────────────────────────────────────────
//  ISSUANCES — Issue form
// ────────────────────────────────────────────────────────────
function onReconTypeChange() {
  const type = document.getElementById('iss-recon-type').value;
  if (type === 'FLAT') document.getElementById('iss-cycles').value = 1;
}

let _lastIssuancePayload = null;

async function submitIssuanceForm() {
  const riderId = document.getElementById('iss-rider-id').value.trim();
  const assetId = document.getElementById('iss-asset-id').value.trim();
  const reconciliationType = document.getElementById('iss-recon-type').value;
  const totalCycles = parseInt(document.getElementById('iss-cycles').value, 10);
  const confirm = document.getElementById('iss-confirm').checked;

  if (!riderId || !assetId) { showToast('Rider ID and Asset ID are required.', 'warning'); return; }

  _lastIssuancePayload = { riderId, assetId, reconciliationType, totalCycles };
  const qs = confirm ? '?confirm=true' : '';

  try {
    const result = await apiFetch(`/api/issuances${qs}`, { method: 'POST', body: _lastIssuancePayload });

    if (result.warning) {
      // Soft duplicate warning
      document.getElementById('iss-duplicate-msg').textContent = result.warning;
      document.getElementById('iss-duplicate-banner').classList.remove('hidden');
      setResult('iss-result', '', true);
      return;
    }

    document.getElementById('iss-duplicate-banner').classList.add('hidden');
    setResult('iss-result', resultPre(result));
    showToast('Asset issued successfully.');
    document.getElementById('iss-rider-id').value = '';
    document.getElementById('iss-asset-id').value = '';
    document.getElementById('iss-confirm').checked = false;
  } catch (e) {
    showToast(e.message, 'error');
    setResult('iss-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

async function resubmitWithConfirm() {
  if (!_lastIssuancePayload) return;
  try {
    const result = await apiFetch('/api/issuances?confirm=true', { method: 'POST', body: _lastIssuancePayload });
    document.getElementById('iss-duplicate-banner').classList.add('hidden');
    setResult('iss-result', resultPre(result));
    showToast('Duplicate confirmed — asset issued.');
  } catch (e) { showToast(e.message, 'error'); }
}

// ────────────────────────────────────────────────────────────
//  ISSUANCES — Lookup by rider
// ────────────────────────────────────────────────────────────
async function lookupRiderIssuances() {
  const riderId = document.getElementById('lookup-rider-id').value.trim();
  const statusFilter = document.getElementById('lookup-status-filter').value;
  if (!riderId) { showToast('Enter a Rider ID.', 'warning'); return; }
  const qs = statusFilter ? `?status=${statusFilter}` : '';
  try {
    const data = await apiFetch(`/api/issuances/${riderId}${qs}`);
    const tbody = document.getElementById('issuances-tbody');
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No issuances found.</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(i => `
      <tr>
        <td><code title="${i._id}">${i._id.slice(-8)}</code></td>
        <td><code title="${i.assetId}">${i.assetId.slice(-8)}</code></td>
        <td>${i.reconciliationType}</td>
        <td>${i.totalCycles}</td>
        <td>${fmtCurrency(i.deductionPerCycle)}</td>
        <td>${fmtCurrency(i.outstandingBalance)}</td>
        <td>${statusBadge(i.status)}</td>
        <td>${fmtDate(i.issuedAt)}</td>
        <td>
          <button class="btn btn-ghost btn-sm" onclick="openReplaceModal('${i._id}')">Replace</button>
        </td>
      </tr>`).join('');
  } catch (e) { showToast(e.message, 'error'); }
}

function openReplaceModal(issuanceId) {
  openModal(
    'Replace Asset',
    `<p>Issuance: <code>${issuanceId}</code></p>
     <div class="form-group" style="margin-top:.75rem">
       <label>Mid-Balance Action *</label>
       <select class="input" id="m-replace-action">
         <option value="waive_remaining">Waive remaining balance</option>
         <option value="pass_to_vendor">Pass remaining to vendor</option>
       </select>
     </div>`,
    'Replace',
    async () => {
      const action = document.getElementById('m-replace-action').value;
      try {
        const result = await apiFetch(`/api/issuances/${issuanceId}/replace?mid_balance_action=${action}`, { method: 'POST' });
        showToast(`Replaced. New issuance: ${result.newIssuanceId}`);
        lookupRiderIssuances();
      } catch (e) { showToast(e.message, 'error'); }
    }
  );
}

// ────────────────────────────────────────────────────────────
//  BULK UPLOAD
// ────────────────────────────────────────────────────────────
let _bulkFile = null;
let _bulkSetupDone = false;

function setupBulkUpload() {
  if (_bulkSetupDone) return;
  _bulkSetupDone = true;
  const dropZone = document.getElementById('drop-zone');
  const fileInput = document.getElementById('bulk-file-input');

  dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    handleBulkFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', () => handleBulkFile(fileInput.files[0]));
}

function onBulkFileSelected(input) {
  handleBulkFile(input.files[0]);
}

function handleBulkFile(file) {
  if (!file) return;
  _bulkFile = file;
  const fn = document.getElementById('drop-filename');
  if (fn) fn.textContent = `${file.name}  (${(file.size / 1024).toFixed(1)} KB)`;
}

function downloadSampleCSV() {
  const csv = ['riderId,assetId,reconciliationType,totalCycles',
    '64f1a2b3c4d5e6f7a8b9c0d1,64f1a2b3c4d5e6f7a8b9c0d2,FLAT,1',
    '64f1a2b3c4d5e6f7a8b9c0d3,64f1a2b3c4d5e6f7a8b9c0d4,EMI,4'].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'sample_issuances.csv' });
  a.click(); URL.revokeObjectURL(a.href);
}

async function submitBulkUpload() {
  if (!_bulkFile) { showToast('Select a CSV file first.', 'warning'); return; }
  const confirm = document.getElementById('bulk-confirm').checked;
  const formData = new FormData();
  formData.append('file', _bulkFile);
  const qs = confirm ? '?confirm=true' : '';
  try {
    const res = await fetch(`${API_BASE}/api/issuances/bulk${qs}`, { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
    const processed = data.processed ?? 0;
    const rejected = data.rejected?.length ?? 0;
    showToast(`Processed: ${processed}, Rejected: ${rejected}`, rejected ? 'warning' : 'success');
    setResult('bulk-results', resultPre(data));
  } catch (e) { showToast(e.message, 'error'); }
}

// ────────────────────────────────────────────────────────────
//  RIDER LOOKUP
// ────────────────────────────────────────────────────────────
async function lookupRiderAssets() {
  const riderId = document.getElementById('rider-lookup-id').value.trim();
  if (!riderId) { showToast('Enter a Rider ID.', 'warning'); return; }
  const container = document.getElementById('rider-result');
  container.innerHTML = '<p style="color:var(--text-muted)">Loading…</p>';
  try {
    const data = await apiFetch(`/api/rider/${riderId}/assets`);
    const assets = data.assets || [];
    if (!assets.length) {
      container.innerHTML = '<p style="color:var(--text-muted)">No active assets for this rider.</p>';
      return;
    }
    container.innerHTML = `
      <div style="margin-bottom:1rem">
        <span class="badge badge-warning">Total Outstanding: ${fmtCurrency(data.totalOutstanding)}</span>
      </div>` +
      assets.map(a => `
      <div class="rider-asset-card">
        <div class="rider-asset-header">
          <span class="rider-asset-name">${a.name}</span>
          ${statusBadge(a.status)}
        </div>
        <div class="rider-asset-details">
          <span>Issuance: <strong><code>${a.issuanceId}</code></strong></span>
          <span>Type: <strong>${a.absorptionType || '-'}</strong></span>
          <span>Cycles: <strong>${a.cyclesCompleted} / ${a.totalCycles}</strong></span>
          <span>Outstanding: <strong>${fmtCurrency(a.outstandingBalance)}</strong></span>
          <span>Deduction/cycle: <strong>${fmtCurrency(a.deductionThisCycle)}</strong></span>
        </div>
      </div>`).join('');
  } catch (e) {
    container.innerHTML = `<p style="color:var(--danger)">${e.message}</p>`;
  }
}

// ────────────────────────────────────────────────────────────
//  SETTLEMENT
// ────────────────────────────────────────────────────────────
function setupSettlement() {
  // Auto-fill current week Mon–Sun in the week-start/end fields
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  monday.setHours(0, 0, 0, 0);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  sunday.setHours(23, 59, 59, 999);

  const toLocal = d => {
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  const ws = document.getElementById('sett-week-start');
  const we = document.getElementById('sett-week-end');
  if (ws) ws.value = toLocal(monday);
  if (we) we.value = toLocal(sunday);

  // Auto-fill status week field with current ISO week
  const year = today.getFullYear();
  const startOfYear = new Date(year, 0, 1);
  const weekNo = Math.ceil(((today - startOfYear) / 86400000 + startOfYear.getDay() + 1) / 7);
  const sw = document.getElementById('sett-status-week');
  if (sw && !sw.value) sw.value = `${year}-W${String(weekNo).padStart(2, '0')}`;
}

async function runWeeklySettlement() {
  const weekStartVal = document.getElementById('sett-week-start').value;
  const weekEndVal = document.getElementById('sett-week-end').value;
  const earningsRaw = document.getElementById('sett-earnings').value.trim();

  if (!weekStartVal || !weekEndVal) { showToast('Week start and end are required.', 'warning'); return; }
  if (!earningsRaw) { showToast('Rider earnings JSON is required.', 'warning'); return; }

  let riderEarnings;
  try { riderEarnings = JSON.parse(earningsRaw); }
  catch { showToast('Invalid JSON in rider earnings.', 'error'); return; }

  openModal(
    'Confirm Settlement Run',
    `<p>Run weekly settlement for <strong>${weekStartVal}</strong> → <strong>${weekEndVal}</strong>?</p>
     <p style="color:var(--text-muted)">${Object.keys(riderEarnings).length} rider(s) in earnings map.</p>`,
    'Run Settlement',
    async () => {
      try {
        const result = await apiFetch('/api/settlement/run-weekly', {
          method: 'POST',
          body: { weekStart: weekStartVal, weekEnd: weekEndVal, riderEarnings },
        });
        showToast('Settlement completed.');
        setResult('sett-run-result', resultPre(result));
      } catch (e) {
        showToast(e.message, 'error');
        setResult('sett-run-result', `<p style="color:var(--danger)">${e.message}</p>`);
      }
    },
    true
  );
}

async function checkSettlementStatus() {
  const week = document.getElementById('sett-status-week').value.trim();
  if (!week) { showToast('Enter a week cycle (e.g. 2026-W12).', 'warning'); return; }
  try {
    const data = await apiFetch(`/api/settlement/status/${encodeURIComponent(week)}`);
    setResult('sett-status-result', resultPre(data));
  } catch (e) {
    showToast(e.message, 'error');
    setResult('sett-status-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

// ────────────────────────────────────────────────────────────
//  VENDORS
// ────────────────────────────────────────────────────────────
async function loadVendorLiabilities() {
  const vendorId = document.getElementById('ven-id-input').value.trim();
  if (!vendorId) { showToast('Enter a Vendor ID.', 'warning'); return; }
  try {
    const data = await apiFetch(`/api/vendors/${vendorId}/liabilities`);
    const tbody = document.getElementById('liabilities-tbody');

    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No liabilities found.</td></tr>';
      document.getElementById('ven-liabilities-summary').classList.add('hidden');
      return;
    }

    // Summary
    const totalOutstanding = data.reduce((s, l) => s + (l.outstandingAmount || 0), 0);
    const totalRecovered = data.reduce((s, l) => s + (l.amountRecovered || 0), 0);
    const summary = document.getElementById('ven-liabilities-summary');
    summary.classList.remove('hidden');
    summary.innerHTML = `
      <div class="stat-card"><div class="stat-label">Outstanding</div><div class="stat-value">${fmtCurrency(totalOutstanding)}</div></div>
      <div class="stat-card"><div class="stat-label">Recovered</div><div class="stat-value">${fmtCurrency(totalRecovered)}</div></div>
      <div class="stat-card"><div class="stat-label">Liabilities</div><div class="stat-value">${data.length}</div></div>`;

    tbody.innerHTML = data.map(l => `
      <tr>
        <td><code title="${l._id}">${l._id?.slice(-8) || '-'}</code></td>
        <td><code title="${l.issuanceId}">${l.issuanceId?.slice(-8) || '-'}</code></td>
        <td><code title="${l.riderId}">${l.riderId?.slice(-8) || '-'}</code></td>
        <td>${fmtCurrency(l.outstandingAmount)}</td>
        <td>${fmtCurrency(l.amountRecovered)}</td>
        <td>${statusBadge(l.status)}</td>
        <td>${l.weekCycle || fmtDate(l.passedAt)}</td>
      </tr>`).join('');
  } catch (e) { showToast(e.message, 'error'); }
}

async function settleVendor() {
  const vendorId = document.getElementById('ven-settle-id').value.trim();
  const amountPaid = parseFloat(document.getElementById('ven-amount').value);
  const note = document.getElementById('ven-note').value.trim() || undefined;
  const performedBy = document.getElementById('ven-performed-by').value.trim() || undefined;

  if (!vendorId) { showToast('Vendor ID is required.', 'warning'); return; }
  if (!amountPaid || amountPaid <= 0) { showToast('Amount must be > 0.', 'warning'); return; }

  openModal(
    'Confirm Vendor Settlement',
    `<p>Record payment of <strong>${fmtCurrency(amountPaid)}</strong> for vendor <strong>${vendorId}</strong>?</p>`,
    'Record Payment',
    async () => {
      try {
        const qs = performedBy ? `?performed_by_user_id=${encodeURIComponent(performedBy)}` : '';
        const result = await apiFetch(`/api/vendors/${vendorId}/settle${qs}`, {
          method: 'POST',
          body: { amountPaid, ...(note ? { note } : {}) },
        });
        showToast('Payment recorded.');
        setResult('ven-settle-result', resultPre(result));
      } catch (e) { showToast(e.message, 'error'); }
    }
  );
}

// ────────────────────────────────────────────────────────────
//  REPORTS
// ────────────────────────────────────────────────────────────
async function loadSettlementReport() {
  const week = document.getElementById('rep-sett-week').value.trim();
  if (!week) { showToast('Enter a week cycle (e.g. 2026-W12).', 'warning'); return; }
  try {
    const data = await apiFetch(`/api/reports/settlement/${encodeURIComponent(week)}`);
    const riderBreakdown = data.riderBreakdown || [];
    const summary = data.summary || {};

    let html = `<div style="margin-bottom:1rem">${resultPre(summary)}</div>`;
    if (riderBreakdown.length) {
      html += `<h4 style="margin:1rem 0 .5rem;color:var(--text-primary)">Rider Breakdown</h4>
        <div class="table-wrap"><table>
          <thead><tr><th>Rider ID</th><th>Entries</th><th>Total Due</th><th>Total Deducted</th><th>Shortfall</th></tr></thead>
          <tbody>${riderBreakdown.map(r => `
            <tr>
              <td><code>${r.riderId}</code></td>
              <td>${r.entries}</td>
              <td>${fmtCurrency(r.totalDue)}</td>
              <td>${fmtCurrency(r.totalDeducted)}</td>
              <td>${fmtCurrency(r.totalShortfall)}</td>
            </tr>`).join('')}
          </tbody></table></div>`;
    }
    setResult('rep-sett-result', html);
  } catch (e) {
    showToast(e.message, 'error');
    setResult('rep-sett-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

async function loadVendorLiabilityReport() {
  try {
    const data = await apiFetch('/api/reports/vendor-liability');
    const vendors = data.vendors || [];
    const totals = data.totals || {};

    let html = `
      <div class="stat-grid sm-grid mb-16">
        <div class="stat-card"><div class="stat-label">Total Outstanding</div><div class="stat-value">${fmtCurrency(totals.totalOutstanding)}</div></div>
        <div class="stat-card"><div class="stat-label">Total Recovered</div><div class="stat-value">${fmtCurrency(totals.totalRecovered)}</div></div>
        <div class="stat-card"><div class="stat-label">Net Outstanding</div><div class="stat-value">${fmtCurrency(totals.netOutstanding)}</div></div>
      </div>`;

    if (vendors.length) {
      html += `<div class="table-wrap"><table>
        <thead><tr><th>Vendor ID</th><th>Outstanding</th><th>Recovered</th><th>Net</th></tr></thead>
        <tbody>${vendors.map(v => `
          <tr>
            <td><code>${v.vendorId || v._id}</code></td>
            <td>${fmtCurrency(v.totalOutstanding)}</td>
            <td>${fmtCurrency(v.totalRecovered)}</td>
            <td>${fmtCurrency(v.netOutstanding)}</td>
          </tr>`).join('')}
        </tbody></table></div>`;
    }
    setResult('rep-vendor-result', html);
  } catch (e) {
    showToast(e.message, 'error');
    setResult('rep-vendor-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

async function loadAuditTrail() {
  const entityId = document.getElementById('rep-audit-entity').value.trim();
  if (!entityId) { showToast('Enter an entity ID.', 'warning'); return; }
  try {
    const data = await apiFetch(`/api/reports/audit/${encodeURIComponent(entityId)}`);
    const logs = data.logs || [];
    let html = `<p style="color:var(--text-muted);margin-bottom:.5rem">${data.totalEntries} entries for <code>${entityId}</code></p>`;
    if (logs.length) {
      html += `<div class="table-wrap"><table>
        <thead><tr><th>Timestamp</th><th>Action</th><th>Type</th><th>Performed By</th><th>Reason</th></tr></thead>
        <tbody>${logs.map(l => `
          <tr>
            <td>${fmtDate(l.timestamp)}</td>
            <td><code>${l.action}</code></td>
            <td>${l.entity_type || '-'}</td>
            <td>${l.performed_by || 'system'}</td>
            <td>${l.reason || '-'}</td>
          </tr>`).join('')}
        </tbody></table></div>`;
    } else {
      html += '<p style="color:var(--text-muted)">No audit logs found.</p>';
    }
    setResult('rep-audit-result', html);
  } catch (e) {
    showToast(e.message, 'error');
    setResult('rep-audit-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

// ────────────────────────────────────────────────────────────
//  ADMIN — Waive
// ────────────────────────────────────────────────────────────
async function waiveIssuance() {
  const issuanceId = document.getElementById('adm-waive-id').value.trim();
  const reason = document.getElementById('adm-waive-reason').value.trim();
  const performedBy = document.getElementById('adm-waive-by').value.trim() || undefined;

  if (!issuanceId || !reason) { showToast('Issuance ID and reason are required.', 'warning'); return; }

  openModal(
    'Confirm Waive',
    `<p>Waive all outstanding deductions for issuance <strong>${issuanceId}</strong>?</p>
     <p style="color:var(--text-muted)">${reason}</p>`,
    'Waive',
    async () => {
      try {
        const result = await apiFetch(`/api/admin/waive/${issuanceId}`, {
          method: 'POST',
          body: { reason, ...(performedBy ? { performed_by_user_id: performedBy } : {}) },
        });
        showToast(result.message || 'Waived.');
        setResult('adm-waive-result', resultPre(result));
      } catch (e) {
        showToast(e.message, 'error');
        setResult('adm-waive-result', `<p style="color:var(--danger)">${e.message}</p>`);
      }
    },
    true
  );
}

// ────────────────────────────────────────────────────────────
//  ADMIN — Adjust Deduction
// ────────────────────────────────────────────────────────────
async function adjustDeduction() {
  const ledgerId = document.getElementById('adm-ded-id').value.trim();
  const amountDeducted = parseFloat(document.getElementById('adm-ded-amount').value);
  const reason = document.getElementById('adm-ded-reason').value.trim();
  const performedBy = document.getElementById('adm-ded-by').value.trim() || undefined;

  if (!ledgerId || isNaN(amountDeducted) || !reason) {
    showToast('Ledger ID, amount, and reason are required.', 'warning'); return;
  }

  try {
    const result = await apiFetch(`/api/admin/deduction/${ledgerId}`, {
      method: 'PATCH',
      body: { amountDeducted, reason, ...(performedBy ? { performed_by_user_id: performedBy } : {}) },
    });
    showToast('Deduction adjusted.');
    setResult('adm-ded-result', resultPre(result));
  } catch (e) {
    showToast(e.message, 'error');
    setResult('adm-ded-result', `<p style="color:var(--danger)">${e.message}</p>`);
  }
}

// ────────────────────────────────────────────────────────────
//  ADMIN — Flag Abuse
// ────────────────────────────────────────────────────────────
async function flagAbuse() {
  const issuanceId = document.getElementById('adm-abuse-id').value.trim();
  const reason = document.getElementById('adm-abuse-reason').value.trim();
  const performedBy = document.getElementById('adm-abuse-by').value.trim() || undefined;

  if (!issuanceId || !reason) { showToast('Issuance ID and reason are required.', 'warning'); return; }

  openModal(
    'Flag Abuse',
    `<p>Override absorption to <strong>SELF</strong> on issuance <strong>${issuanceId}</strong>?</p>
     <p style="color:var(--text-muted)">${reason}</p>`,
    'Flag Abuse',
    async () => {
      try {
        const result = await apiFetch(`/api/admin/flag-abuse/${issuanceId}`, {
          method: 'POST',
          body: { reason, ...(performedBy ? { performed_by_user_id: performedBy } : {}) },
        });
        showToast(result.message || 'Flagged.');
        setResult('adm-abuse-result', resultPre(result));
      } catch (e) {
        showToast(e.message, 'error');
        setResult('adm-abuse-result', `<p style="color:var(--danger)">${e.message}</p>`);
      }
    },
    true
  );
}

// ────────────────────────────────────────────────────────────
//  ADMIN — Year-End Write-Off
// ────────────────────────────────────────────────────────────
function onDryRunChange() {
  const isDry = document.getElementById('adm-dry-run').checked;
  document.getElementById('adm-writeoff-btn').textContent = isDry ? 'Preview Write-Off' : 'Execute Write-Off';
}

async function yearEndWriteOff() {
  const dateVal = document.getElementById('adm-writeoff-date').value;
  const performedBy = document.getElementById('adm-writeoff-by').value.trim() || undefined;
  const dryRun = document.getElementById('adm-dry-run').checked;

  if (!dateVal) { showToast('Financial year-end date is required.', 'warning'); return; }

  const confirmMsg = dryRun
    ? `Preview year-end write-off as of <strong>${dateVal}</strong> (no changes).`
    : `<strong>Execute</strong> year-end write-off as of <strong>${dateVal}</strong>. This will write off active SELF issuances with no activity since that date.`;

  openModal(
    dryRun ? 'Preview Write-Off' : 'Execute Write-Off',
    `<p>${confirmMsg}</p>`,
    dryRun ? 'Preview' : 'Execute',
    async () => {
      try {
        const result = await apiFetch('/api/admin/year-end-writeoff', {
          method: 'POST',
          body: {
            financialYearEnd: new Date(dateVal).toISOString(),
            dry_run: dryRun,
            ...(performedBy ? { performed_by_user_id: performedBy } : {}),
          },
        });
        showToast(result.message || (dryRun ? 'Preview complete.' : 'Write-off executed.'));
        setResult('adm-writeoff-result', resultPre(result));
      } catch (e) {
        showToast(e.message, 'error');
        setResult('adm-writeoff-result', `<p style="color:var(--danger)">${e.message}</p>`);
      }
    },
    !dryRun
  );
}

// ────────────────────────────────────────────────────────────
//  API health check
// ────────────────────────────────────────────────────────────
async function checkHealth() {
  try {
    await apiFetch('/');
    const dot = document.getElementById('health-dot');
    const label = document.getElementById('health-label');
    if (dot) { dot.style.background = 'var(--success)'; }
    if (label) label.textContent = 'API Online';
  } catch {
    const dot = document.getElementById('health-dot');
    const label = document.getElementById('health-label');
    if (dot) dot.style.background = 'var(--danger)';
    if (label) label.textContent = 'API Offline';
  }
}

// ────────────────────────────────────────────────────────────
//  Init
// ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  activateSection('dashboard');
  checkHealth();
  // Re-check health every 30s
  setInterval(checkHealth, 30000);
});
