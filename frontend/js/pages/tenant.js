/* IdPVault - pages/tenant.js: tenant CRUD, snapshots & diff, config restore, snapshot browser, Users & Access. Split from index.html (v1.2 Phase 1a). */
/* ---------- tenants ---------- */
function toggleAdd(){
  // the tenant form is shared between fleet (add) and the tenant Settings page (edit);
  // pull it back into the fleet host and reset to add-mode if it was last used elsewhere
  const f = document.getElementById('addform'), host = document.getElementById('fleet_formhost');
  if(f.parentElement !== host){ host.appendChild(f); f.classList.add('hidden'); resetForm(); }
  f.classList.toggle('hidden');
  if(f.classList.contains('hidden')) resetForm(); else onProviderChange();
}
function cancelTenantForm(){
  const f = document.getElementById('addform');
  const onSettings = f.parentElement === document.getElementById('t_settings_formhost');
  f.classList.add('hidden'); resetForm();
  if(onSettings && currentTenantId) location.hash = '#/t/' + currentTenantId + '/overview';
}
function mountTenantSettings(t){
  const host = document.getElementById('t_settings_formhost');
  const f = document.getElementById('addform');
  if(f.parentElement !== host){ host.innerHTML = ''; host.appendChild(f); }
  editTenant(t.id);
}
async function renderTenantOverview(t){
  const cards = document.getElementById('t_overview_cards');
  if(!cards) return;
  cards.innerHTML = '<div class="card"><div class="sub">Loading…</div></div>';
  document.getElementById('t_overview_banner').innerHTML = '';
  document.getElementById('t_overview_grid').innerHTML = '';
  document.getElementById('t_overview_body').innerHTML = '';
  let state = null, dash = null;
  try{ state = await api(`/tenants/${t.id}/state/summary`); }catch{}
  try{ dash = await api('/dashboard/summary'); }catch{}
  renderTenantOverviewView(t, state, dash);
}
function renderTenantOverviewView(t, state, dash){
  const cards = document.getElementById('t_overview_cards');
  const banner = document.getElementById('t_overview_banner');
  const grid = document.getElementById('t_overview_grid');
  const body = document.getElementById('t_overview_body');
  if(!cards || !grid) return;
  const canW = me.role === 'admin' || me.role === 'org_admin';
  const inactive = t.active === false;
  const dt = dash && dash.tenants ? dash.tenants.find(x => x.id === t.id) : null;
  const lastRun = dt && dt.last_run;
  const issues = [];
  if(inactive) issues.push('license paused');
  if(!t.schedule_cron) issues.push('no backup schedule');
  if(!lastRun) issues.push('no backups yet');
  else if(lastRun.status !== 'ok') issues.push('last backup ' + lastRun.status);
  const ok = state && state.available;
  const drift = ok && state.drift ? (state.drift.added + state.drift.removed + state.drift.changed) : null;
  const checked = ok && state.checked_at ? new Date(state.checked_at) : null;
  const ago = checked ? Math.max(0, Math.round((Date.now() - checked.getTime()) / 60000)) : null;
  const agoTxt = ago == null ? '' : (ago === 0 ? 'just now' : ago + 'm ago');
  cards.innerHTML = `
    <div class="card ${issues.length ? 'warn' : 'good'}"><div class="lbl2">Backup health</div><div class="big" style="font-size:1.3rem">${inactive ? 'Paused' : issues.length ? 'Attention' : 'Excellent'}</div><div class="sub">${issues.length ? esc(issues.join(' · ')) : 'scheduled and running clean'}</div></div>
    <div class="card"><div class="lbl2">Backup schedule</div><div class="big" style="font-size:1.1rem">${esc(cronLabel(t.schedule_cron))}</div><div class="sub">last backup: ${lastRun ? fmtSnapDay(lastRun.ts) : 'never'} · keep ${t.retention_keep}</div></div>
    <div class="card ${drift ? 'warn' : ''}"><div class="lbl2">Unbacked changes</div><div class="big">${drift == null ? '-' : drift}</div><div class="sub">${checked ? 'config vs latest backup · checked ' + agoTxt : 'awaiting first live check'}</div></div>`;
  banner.innerHTML = (drift && state.latest_snapshot)
    ? `<section class="panel" style="border-color:var(--amber);margin-bottom:20px"><p style="font-size:.88rem">Your most recent backup (${fmtSnap(state.latest_snapshot)}) is out of sync with current state - <b>${drift}</b> change${drift === 1 ? '' : 's'} not yet backed up.${canW && !inactive ? ` <button class="primary" style="margin-left:10px" onclick="backupNow(${t.id}, this)">Backup now</button>` : ''}</p></section>` : '';
  const cats = ok ? Object.keys(state.counts || {}).sort() : [];
  const per = (ok && state.categories) || {};
  grid.innerHTML = cats.length ? cats.map(rt => {
    const d = per[rt];
    const badge = d ? ` <span style="font-size:.72rem">${d.added ? `<span style="color:var(--green)">+${d.added}</span> ` : ''}${d.removed ? `<span style="color:var(--red)">−${d.removed}</span> ` : ''}${d.changed ? `<span style="color:var(--amber)">~${d.changed}</span>` : ''}</span>` : '';
    return `<div class="card" style="cursor:pointer" onclick="overviewOpenCat('${esc(rt)}')"><div class="lbl2">${esc(rt.replace(/_/g,' '))}</div><div class="big">${state.counts[rt]}${badge}</div></div>`;
  }).join('') : `<div class="card"><div class="lbl2">Live view warming up</div><div class="sub">The first check runs within a few minutes${canW ? ', or click Refresh from provider' : ''}. Category counts appear here with drift badges once ready.</div></div>`;
  const src = document.getElementById('t_overview_src');
  if(src) src.textContent = ok ? (state.source === 'live' ? `- live from provider, checked ${agoTxt}` : '- as of the latest backup') : '';
  body.innerHTML =
    (inactive ? '<p class="st-failed" style="font-size:.85rem;margin-bottom:10px">License limit reached - backup and restore are paused for this tenant. Manage your license in Administration &gt; License.</p>' : '') +
    `<p class="muted" style="font-size:.85rem">Users &amp; Access backup: <b>${t.identity_enabled ? 'enabled' : 'disabled'}</b>${t.identity_enabled && t.identity_schedule_cron ? ' · ' + esc(cronLabel(t.identity_schedule_cron)) : ''} · <span class="tag ${t.provider}">${t.provider}</span> ${esc(t.slug)}${t.org_name ? ' · ' + esc(t.org_name) : ''}</p>` +
    (canW && !inactive ? `<div style="margin-top:12px"><button class="primary" onclick="backupNow(${t.id}, this)">Backup now</button> <button onclick="location.hash='#/t/${t.id}/backups'">View backups</button></div>` : '');
}
function overviewOpenCat(rt){
  window._exJumpCat = rt;
  window._exJumpSnap = 'current';   // Overview tiles show live state; land on the live view
  location.hash = '#/t/' + currentTenantId + '/explorer';
}
async function overviewRefresh(){
  const btn = document.getElementById('t_ov_refresh');
  const t = _tenants.find(x => x.id === currentTenantId); if(!t) return;
  btn.disabled = true; const old = btn.innerHTML; btn.innerHTML = 'Refreshing…';
  try{
    const state = await api(`/tenants/${t.id}/state/refresh`, {method:'POST'});
    const dash = await api('/dashboard/summary').catch(() => null);
    renderTenantOverviewView(t, state, dash);
  }catch(e){ toast(e.message, true); }
  btn.disabled = false; btn.innerHTML = old;
}
const LIC_TIP_TENANT = 'Over your license&#39;s tenant limit - backup &amp; restore paused for this tenant. Add a license in Administration &gt; License';
const LIC_TIP_IDENTITY = 'Users &amp; Access backup &amp; restore requires a paid license - add one in Administration &gt; License';
const MSP_TIP = 'Contact your MSP administrator to take this action';
async function loadTenants(){
  const tb = document.getElementById('tenantbody');
  const isAdmin = me.role === 'admin';
  tb.innerHTML = skelRows(6);
  try {
    const ts = await api('/tenants'); _tenants = ts;
    if(isAdmin){ try { _license = await api('/license'); } catch {} }
    const atLimit = _license && _license.max_tenants != null && ts.length >= _license.max_tenants;
    const addBtn = document.getElementById('addtenantbtn');
    if(addBtn && isAdmin){ addBtn.disabled = !!atLimit;
      addBtn.title = atLimit ? 'Tenant limit reached for your license - upgrade in Administration > License' : '';
      addBtn.innerHTML = '+ Add tenant' + (atLimit ? ' ' + TIPI : ''); }
    if(!ts.length){ tb.innerHTML = emptyRow(6, EI.db, 'No tenants yet', isAdmin?'<button class="primary" onclick="toggleAdd()">+ Add your first tenant</button>':''); return; }
    const identOk = ((me && me.features) || []).includes('identity');
    const canW = me.role==='admin' || me.role==='org_admin';
    const isViewer = me.role==='org_viewer';
    tb.innerHTML = ts.map(t => {
      const inactive = t.active === false;
      const lockT = inactive ? `disabled title="${LIC_TIP_TENANT}"` : '';
      const lockI = inactive ? `disabled title="${LIC_TIP_TENANT}"`
                  : (!identOk ? `disabled title="${LIC_TIP_IDENTITY}"` : '');
      return `<tr>
      <td>${esc(t.name)}${t.org_name?` <span class="muted" style="font-size:.72rem">· ${esc(t.org_name)}</span>`:''}${inactive?' <span class="muted" style="font-size:.72rem" title="'+LIC_TIP_TENANT+'">(license paused) <span class="tipi">ⓘ</span></span>':''}</td>
      <td><span class="tag ${t.provider}">${t.provider}</span></td>
      <td class="muted">${esc(t.slug)}</td>
      <td class="muted">${esc(cronLabel(t.schedule_cron))}</td>
      <td class="muted">${t.retention_keep}</td>
      <td style="white-space:nowrap">${canW ? `
        <button ${lockT} onclick="backupNow(${t.id}, this)">Backup now${lockT?' '+TIPI:''}</button>
        <button onclick="location.hash='#/t/${t.id}/settings'">Edit</button>` : isViewer ? `
        <button disabled title="${MSP_TIP}">Backup now ${TIPI}</button>
        <button disabled title="${MSP_TIP}">Edit ${TIPI}</button>` : ''}
        <button onclick="location.hash='#/t/${t.id}/backups'">Snapshots</button>
        ${canW ? (t.supports_identity===false ? `<button disabled title="Users &amp; Access backup isn't supported for this provider yet">Users &amp; Access ${TIPI}</button>` : `<button ${lockI} onclick="location.hash='#/t/${t.id}/identity'">Users &amp; Access${lockI?' '+TIPI:''}</button>`) : isViewer ? `<button disabled title="${MSP_TIP}">Users &amp; Access ${TIPI}</button>` : ''}
      </td></tr>`; }).join('');
  } catch(e){ tb.innerHTML = `<tr><td colspan="6" class="muted">Failed to load: ${esc(e.message)}</td></tr>`; }
}
function editTenant(id){
  const t = _tenants.find(x=>x.id===id); if(!t) return;
  editingId = id;
  document.getElementById('addform').classList.remove('hidden');
  document.getElementById('formtitle').textContent = `Editing "${t.name}" - slug and provider are fixed; leave token blank to keep the current one.`;
  document.getElementById('f_name').value = t.name;
  document.getElementById('f_slug').value = t.slug;
  document.getElementById('f_provider').value = t.provider;
  onProviderChange();
  document.getElementById('f_url').value = t.base_url || '';
  document.getElementById('f_token').value = '';
  document.getElementById('f_token').placeholder = 'leave blank to keep current token';
  document.getElementById('f_clientid').value = '';
  document.getElementById('f_clientsecret').value = '';
  if(t.provider==='auth0'){ document.getElementById('f_clientid').placeholder='leave blank to keep current'; document.getElementById('f_clientsecret').placeholder='leave blank to keep current'; }
  schedSet('fs', t.schedule_cron || null);
  document.getElementById('f_keep').value = t.retention_keep;
  document.getElementById('f_dburl').value = '';
  document.getElementById('f_dbevents').value = (t.db_dump_exclude_events ? 'true' : 'false');
  document.getElementById('f_ident').value = (t.identity_enabled && !document.getElementById('f_ident').disabled) ? 'true' : 'false';
  schedSet('fi', t.identity_schedule_cron || null);
  document.getElementById('f_identkeep').value = t.identity_retention_keep || 14;
  document.getElementById('f_dburl').placeholder = t.db_dr ? 'full-DR configured - blank keeps it, a space clears it' : 'postgresql://… - optional, self-hosted only';
  document.getElementById('f_slug').disabled = true;
  document.getElementById('f_provider').disabled = true;
  document.getElementById('f_delete').classList.remove('hidden');
  fillTenantOrg(t.org_id);
}
function onProviderChange(){
  const p = document.getElementById('f_provider').value;
  const show = (id,on)=>document.getElementById(id).classList.toggle('hidden', !on);
  const isAuth0 = p==='auth0', hasIdentity = true;  // all providers support Users & Access now
  show('fd_token', !isAuth0); show('fd_clientid', isAuth0); show('fd_clientsecret', isAuth0);
  show('fd_dburl', p==='authentik'); show('fd_dbevents', p==='authentik');
  show('fd_ident', hasIdentity); 
  // License gate: Community has no 'identity' feature - lock the control instead
  // of letting the save fail with a 402.
  const licIdent = ((me && me.features) || []).includes('identity');
  const identSel = document.getElementById('f_ident');
  identSel.disabled = !licIdent;
  if(!licIdent) identSel.value = 'false';
  document.getElementById('f_ident_note').classList.toggle('hidden', licIdent);
  show('fd_identcron', hasIdentity && licIdent); show('fd_identkeep', hasIdentity && licIdent);
  document.getElementById('f_url').placeholder = isAuth0 ? 'https://acme.us.auth0.com' : p==='okta' ? 'https://acme.okta.com' : 'https://auth.acme.com';
  if(!editingId){
    document.getElementById('f_token').placeholder = p==='okta' ? 'paste SSWS API token - encrypted at rest' : 'paste token - encrypted at rest';
    const pname = isAuth0 ? 'Auth0' : p==='okta' ? 'Okta' : 'Authentik';
    document.getElementById('f_name').placeholder = `Acme ${pname}`;
    document.getElementById('f_slug').placeholder = `acme-${p}`;
  }
}
function resetForm(){
  editingId = null;
  document.getElementById('formtitle').textContent = '';
  document.getElementById('f_slug').disabled = false;
  document.getElementById('f_provider').disabled = false;
  document.getElementById('f_token').placeholder = 'paste token - encrypted at rest';
  ['f_name','f_slug','f_url','f_token','f_clientid','f_clientsecret','f_dburl'].forEach(i=>document.getElementById(i).value='');
  document.getElementById('f_provider').value='authentik';
  document.getElementById('f_ident').value='false';
  document.getElementById('f_dbevents').value='false';
  document.getElementById('f_identkeep').value=14;
  document.getElementById('f_keep').value = 30;
  document.getElementById('f_delete').classList.add('hidden');
  schedSet('fs', null); schedSet('fi', null);
  fillTenantOrg(null);
  // prefill from org defaults (Settings) for new tenants
  api('/settings').then(s=>{
    if(editingId) return;
    if(s.default_schedule_cron) schedSet('fs', s.default_schedule_cron);
    if(s.default_identity_schedule_cron) schedSet('fi', s.default_identity_schedule_cron);
    if(s.default_retention_keep) document.getElementById('f_keep').value = s.default_retention_keep;
  }).catch(()=>{});
  onProviderChange();
}
async function saveTenant(){
  const prov = v('f_provider');
  const auth0cred = ()=>{ const cid=v('f_clientid'), sec=v('f_clientsecret'); return (cid&&sec) ? cid+':'+sec : ''; };
  if(editingId){
    const body = { name: v('f_name'), base_url: v('f_url'),
      schedule_cron: schedGet('fs'), retention_keep: parseInt(v('f_keep')||'30'),
      identity_enabled: v('f_ident')==='true', identity_schedule_cron: schedGet('fi'),
      identity_retention_keep: parseInt(v('f_identkeep')||'14') };
    if(!document.getElementById('fd_org').classList.contains('hidden')) body.org_id = v('f_org') ? parseInt(v('f_org')) : null;
    const tok = prov==='auth0' ? auth0cred() : v('f_token');
    if(tok) body.api_token = tok;
    if(prov==='authentik'){ const dbraw = document.getElementById('f_dburl').value; if(dbraw!=='') body.db_url = dbraw.trim(); body.db_dump_exclude_events = v('f_dbevents')==='true'; }
    try {
      await api(`/tenants/${editingId}`, {method:'PATCH', body: JSON.stringify(body)});
      toast('Tenant updated.' + (body.api_token ? ' Credentials rotated.' : ''));
      loadTenants(); renderTenantSelector();
    } catch(e){ toast('Update failed: '+e.message, true); }
    return;
  }
  if(prov==='auth0' && !auth0cred()) return toast('Auth0 needs both Client ID and Client Secret', true);
  const body = {
    name: v('f_name'), slug: v('f_slug'), provider: prov,
    base_url: v('f_url'), api_token: prov==='auth0' ? auth0cred() : v('f_token'),
    schedule_cron: schedGet('fs'), retention_keep: parseInt(v('f_keep')||'30'),
    db_url: prov==='authentik' ? (v('f_dburl') || null) : null,
    db_dump_exclude_events: prov==='authentik' && v('f_dbevents')==='true',
    identity_enabled: v('f_ident')==='true', identity_schedule_cron: schedGet('fi'),
    identity_retention_keep: parseInt(v('f_identkeep')||'14')
  };
  if(!document.getElementById('fd_org').classList.contains('hidden')) body.org_id = v('f_org') ? parseInt(v('f_org')) : null;
  if(!body.name || !body.slug || !body.base_url || !body.api_token) return toast('Name, slug, base URL and credentials are required', true);
  try {
    await api('/tenants', {method:'POST', body: JSON.stringify(body)});
    toast(`Tenant "${body.name}" created - credentials encrypted and stored.`);
    toggleAdd(); loadTenants(); renderTenantSelector();
  } catch(e){ toast('Create failed: '+e.message, true); }
}
async function backupNow(id, btn){
  btn.disabled = true; btn.textContent = 'Backing up…';
  try {
    const r = await api(`/tenants/${id}/backup`, {method:'POST'});
    const c = r.manifest.counts, total = Object.values(c).reduce((a,b)=>a+b,0);
    toast(`Snapshot ${fmtSnap(r.manifest.timestamp)} complete - ${total} objects across ${Object.keys(c).length} types.` + (r.drift_detected ? ' ⚠ Drift detected vs previous snapshot.' : ''));
    if(snapTenantId === id) showSnaps(id, document.getElementById('snaptenant').textContent);
    loadDashboard();
  } catch(e){ toast('Backup failed: '+e.message, true); }
  btn.disabled = false; btn.textContent = 'Backup now';
}
async function deleteFromForm(){
  const t = _tenants.find(x=>x.id===editingId); if(!t) return;
  if(!confirm(`Delete tenant "${t.name}"? Snapshots on disk are kept.`)) return;
  try {
    await api(`/tenants/${editingId}`, {method:'DELETE'});
    toast(`Tenant "${t.name}" deleted.`);
    document.getElementById('addform').classList.add('hidden'); resetForm();
    try { _tenants = await api('/tenants'); } catch {}
    currentTenantId = null;
    renderTenantSelector(); location.hash = defaultRoute(); route();
  }
  catch(e){ toast('Delete failed: '+e.message, true); }
}

/* ---------- snapshots & diff ---------- */
async function showSnaps(id, slug){
  snapTenantId = id; selectedSnaps = [];
  document.getElementById('snaptenant').textContent = slug;
  document.getElementById('snappanel').classList.remove('hidden');
  window._snapSlug = slug;
  updateSnapButtons();
  const sb = document.getElementById('snapbody');
  sb.innerHTML = skelRows(7);
  try {
    const snaps = await api(`/tenants/${id}/snapshots`);
    if(!snaps.length){ sb.innerHTML = emptyRow(7, EI.db, 'No snapshots yet - run a backup to create one.'); return; }
    const admin = me.role==='admin' || me.role==='org_admin';
    sb.innerHTML = snaps.slice().reverse().map(s => `<tr class="snaprow" data-ts="${s.ts}">
      <td><input type="checkbox" tabindex="-1" onchange="selSnap(this)"></td>
      <td>${fmtSnap(s.ts)}</td>
      <td>${s.objects || 0}</td>
      <td class="muted">${fmtBytes(s.size || 0)}</td>
      <td class="muted">${s.db_dump_size != null ? fmtBytes(s.db_dump_size) : '-'}</td>
      <td class="chgcell" data-ts="${s.ts}"><span class="muted">…</span></td>
      <td style="text-align:right"><button onclick="openBrowse('${s.ts}')">Browse</button> ${admin?(_tenants.find(x=>x.id===id)?.active===false?`<button disabled title="${LIC_TIP_TENANT}">Restore… ${TIPI}</button>`:`<button onclick="openRestore('${s.ts}')">Restore…</button>`):''}</td></tr>`).join('');
    fillSnapChanges(id);
  } catch(e){ sb.innerHTML = `<tr><td colspan="7" class="muted">Failed: ${esc(e.message)}</td></tr>`; }
}
async function fillSnapChanges(id){
  for(const el of document.querySelectorAll('#snapbody .chgcell')){
    if(snapTenantId !== id) return;   // navigated away mid-fill
    try{
      const c = await api(`/tenants/${id}/snapshots/${el.dataset.ts}/changes`);
      el.innerHTML = c.first ? '<span class="muted">first snapshot</span>'
        : (c.added || c.removed || c.changed)
          ? `<span class="add">+${c.added}</span> <span class="rem">−${c.removed}</span> <span class="chg">~${c.changed}</span>`
          : '<span class="muted">none</span>';
    }catch{ el.innerHTML = '<span class="muted">-</span>'; }
  }
}
function updateSnapButtons(){
  const n = selectedSnaps.length;
  const diffBtn = document.getElementById('diffbtn');
  if(diffBtn){ diffBtn.disabled = n !== 2;
    diffBtn.innerHTML = (n === 2 ? 'Compare selected' : 'Compare (select 2)') + ' ' + TIPI; }
  const del = document.getElementById('delsnapsbtn');
  if(del){ del.classList.toggle('hidden', me.role !== 'admin' || n === 0);
    del.textContent = `Delete selected (${n})`; }
  const all = document.getElementById('snapall');
  if(all){ const rows = document.querySelectorAll('#snapbody .snaprow').length;
    all.checked = rows > 0 && n === rows; }
}
function selAllSnaps(on){
  selectedSnaps = [];
  document.querySelectorAll('#snapbody .snaprow').forEach(r => {
    const cb = r.querySelector('input'); cb.checked = on; r.classList.toggle('sel', on);
    if(on) selectedSnaps.push(r.dataset.ts);
  });
  updateSnapButtons();
}
let _delCtx = null;
function askDelete(msg, fn){
  _delCtx = fn;
  document.getElementById('cdm_msg').textContent = msg;
  document.getElementById('cdm_pw').value = '';
  document.getElementById('confirmdelmodal').classList.remove('hidden');
  document.getElementById('cdm_pw').focus();
}
function closeConfirmDel(){ document.getElementById('confirmdelmodal').classList.add('hidden'); _delCtx = null; }
function confirmDelGo(){
  const pw = v('cdm_pw');
  if(!pw) return toast('Enter your password to confirm the deletion', true);
  const fn = _delCtx; closeConfirmDel();
  if(fn) fn(pw);
}
function deleteSnapshots(){
  const n = selectedSnaps.length; if(!n) return;
  askDelete(`Permanently delete ${n} selected backup(s) for "${window._snapSlug}"? This removes the snapshot files, including any Full-DR database dumps. This cannot be undone.`, async pw => {
    try{
      const r = await api(`/tenants/${snapTenantId}/snapshots/delete`, {method:'POST', body: JSON.stringify({timestamps: selectedSnaps, password: pw})});
      toast(`${r.deleted.length} backup(s) deleted.`);
      showSnaps(snapTenantId, window._snapSlug);
    }catch(e){ toast('Delete failed: ' + e.message, true); }
  });
}
function selSnap(cb){
  const row = cb.closest('tr'), ts = row.dataset.ts;
  if(cb.checked){ if(!selectedSnaps.includes(ts)) selectedSnaps.push(ts); row.classList.add('sel'); }
  else { selectedSnaps = selectedSnaps.filter(x=>x!==ts); row.classList.remove('sel'); }
  updateSnapButtons();
}
function objLabel(o){ return (o && (o.name || o.slug || o.username || o.label || o.title || o.id || o.pk)) || '-'; }
function changedFieldList(c){
  const b = c.before || {}, a = c.after || {}, out = [];
  new Set([...Object.keys(b), ...Object.keys(a)]).forEach(k => {
    if(k.endsWith('_obj')) return;
    if(JSON.stringify(b[k]) !== JSON.stringify(a[k])) out.push(k);
  });
  return out;
}
const DIFF_TAG = {added:'<span class="tag ok">added</span>', removed:'<span class="tag off">removed</span>', changed:'<span class="tag pending">changed</span>'};
async function runDiff(){
  const [a,b] = selectedSnaps.slice().sort();
  document.getElementById('difflabel').textContent = `${fmtSnap(a)} → ${fmtSnap(b)}`;
  document.getElementById('diffpanel').classList.remove('hidden');
  document.getElementById('difftablewrap').classList.remove('hidden');
  document.getElementById('diffout').classList.add('hidden');
  document.getElementById('diffpanel').scrollIntoView({behavior:'smooth'});
  const rows = document.getElementById('diffrows');
  rows.innerHTML = skelRows(5);
  try {
    const d = await api(`/tenants/${snapTenantId}/diff?old=${a}&new=${b}`);
    window._diffData = d;
    let add=0, rem=0, chg=0; const out=[];
    const mk = (kind, rt, obj, fields, ref) => `<tr>
      <td>${DIFF_TAG[kind]}</td><td>${esc(rt.replace(/_/g,' '))}</td>
      <td>${esc(objLabel(obj))}</td>
      <td class="muted" style="font-size:.78rem">${fields && fields.length ? esc(fields.slice(0,8).join(', ')) + (fields.length>8 ? ' …' : '') : '-'}</td>
      <td><button onclick="diffView('${ref}')" title="See the full object JSON (before and after for changed objects)">View ${TIPI}</button></td></tr>`;
    Object.keys(d).sort().forEach(rt => {
      const x = d[rt];
      x.added.forEach((o,i)=>{ add++; out.push(mk('added', rt, o, null, `a:${rt}:${i}`)); });
      x.removed.forEach((o,i)=>{ rem++; out.push(mk('removed', rt, o, null, `r:${rt}:${i}`)); });
      x.changed.forEach((c,i)=>{ chg++; out.push(mk('changed', rt, c.after || c.before, changedFieldList(c), `c:${rt}:${i}`)); });
    });
    document.getElementById('diffstat').innerHTML = out.length
      ? `<span class="add">+${add} added</span><span class="rem">−${rem} removed</span><span class="chg">~${chg} changed</span>`
      : '<span class="muted">No differences - configurations are identical.</span>';
    rows.innerHTML = out.join('');
    if(!out.length) document.getElementById('difftablewrap').classList.add('hidden');
  } catch(e){ rows.innerHTML = `<tr><td colspan="5" class="muted">Compare failed: ${esc(e.message)}</td></tr>`; }
}
function diffView(ref){
  const pre0 = document.getElementById('diffout');
  if(window._diffOpenRef === ref && !pre0.classList.contains('hidden')){
    pre0.classList.add('hidden'); window._diffOpenRef = null; return;   // second click collapses
  }
  window._diffOpenRef = ref;
  const i1 = ref.indexOf(':'), i2 = ref.lastIndexOf(':');
  const kind = ref.slice(0, i1), rt = ref.slice(i1+1, i2), i = +ref.slice(i2+1);
  const x = (window._diffData || {})[rt]; if(!x) return;
  const payload = kind === 'a' ? x.added[i] : kind === 'r' ? x.removed[i]
    : {before: x.changed[i].before, after: x.changed[i].after};
  const pre = document.getElementById('diffout');
  pre.textContent = JSON.stringify(payload, null, 2);
  pre.classList.remove('hidden');
  pre.scrollIntoView({behavior:'smooth'});
}

async function fillTenantOrg(val){
  const d = document.getElementById('fd_org'); if(!d) return;
  if(!(me && me.role==='admin' && (me.features||[]).includes('msp'))){ d.classList.add('hidden'); return; }
  try { _orgs = await api('/orgs'); } catch { d.classList.add('hidden'); return; }
  if(!_orgs.length){ d.classList.add('hidden'); return; }
  document.getElementById('f_org').innerHTML = '<option value="">No org (internal tenant)</option>' +
    _orgs.map(o=>`<option value="${o.id}">${esc(o.name)}</option>`).join('');
  document.getElementById('f_org').value = val ? String(val) : '';
  d.classList.remove('hidden');
}
/* ---------- restore ---------- */
let _restoreCtx = null;
let _restoreItems = [];
async function openRestore(ts){
  _restoreCtx = { tenantId: snapTenantId, snap: ts };
  document.getElementById('r_tenant').textContent = window._snapSlug || '';
  document.getElementById('r_snap').textContent = fmtSnap(ts);
  document.getElementById('r_summary').textContent = '';
  document.getElementById('r_items').innerHTML = '';
  document.getElementById('r_applybtn').classList.add('hidden');
  const src = _tenants.find(x=>x.id===snapTenantId);
  const sameprov = _tenants.filter(x=>!src || x.provider===src.provider);
  document.getElementById('r_target').innerHTML = sameprov.map(x=>`<option value="${x.id}"${x.id===snapTenantId?' selected':''}>${esc(x.name)}${x.id===snapTenantId?' (same tenant)':''}</option>`).join('');
  document.getElementById('restoremodal').classList.remove('hidden');
}
function closeRestore(){ document.getElementById('restoremodal').classList.add('hidden'); _restoreCtx=null; }
function renderRestore(res){
  const s = res.summary;
  _restoreItems = res.items;
  const isDry = s.mode==='dry_run';
  const acts = s.actions||{}, sts = s.statuses||{};
  document.getElementById('r_summary').innerHTML =
    `<b>${isDry?'Preview':'Applied'}:</b> ${s.total} objects - ` +
    Object.entries(acts).map(([k,v])=>`<span class="act-${k}">${v} ${k}</span>`).join(' · ') +
    (s.mode==='apply' ? ' - ' + Object.entries(sts).map(([k,v])=>`<span class="st-${k}">${v} ${k}</span>`).join(' · ') : '');
  const hasActionable = res.items.some(it=>(it.action==='create'||it.action==='update') && it.restorable!==false);
  const selbar = (isDry && hasActionable)
    ? `<div style="margin:8px 0 4px"><button onclick="setAllRestore(true)">Select all</button> <button onclick="setAllRestore(false)">Unselect all</button></div>` : '';
  const head = `<div class="restore-item" style="font-weight:600;border-bottom:1px solid var(--border)"><div></div><div>ACTION</div><div>OBJECT</div><div>STATUS</div></div>`;
  document.getElementById('r_items').innerHTML = selbar + head +
    res.items.map((it,i)=>{
      const actionable = (it.action==='create'||it.action==='update') && it.restorable!==false;
      const box = (isDry && actionable) ? `<input type="checkbox" class="r-sel" data-i="${i}" checked onchange="updateRestoreCount()">` : '';
      return `<div class="restore-item">
      <div>${box}</div>
      <div class="act-${it.action}">${it.action}</div>
      <div>${esc(it.resource_type)} / ${esc(it.object_name||it.object_id||'-')}${it.changed_fields&&it.changed_fields.length?` <span class="muted">(${it.changed_fields.slice(0,5).join(', ')})</span>`:''}</div>
      <div class="st-${it.status}">${it.status}${it.error?': '+esc(it.error).slice(0,60):''}</div></div>`;
    }).join('');
}
function setAllRestore(v){ document.querySelectorAll('#r_items .r-sel').forEach(b=>b.checked=v); updateRestoreCount(); }
function updateRestoreCount(){
  const boxes=[...document.querySelectorAll('#r_items .r-sel')];
  const n=boxes.filter(b=>b.checked).length;
  const btn=document.getElementById('r_applybtn');
  if(boxes.length>0){ btn.classList.remove('hidden'); btn.textContent=`Apply restore (${n} change${n===1?'':'s'})`; btn.disabled=(n===0); }
  else btn.classList.add('hidden');
}
async function restorePreview(){
  document.getElementById('r_summary').textContent = 'Running preview…';
  try {
    const tgt = parseInt(document.getElementById('r_target').value);
    const payload = {snapshot_ts:_restoreCtx.snap}; if(tgt!==_restoreCtx.tenantId) payload.target_tenant_id = tgt;
    const res = await api(`/tenants/${_restoreCtx.tenantId}/restore/preview`, {method:'POST', body: JSON.stringify(payload)});
    renderRestore(res);
    if(_restorePreselect){
      // Explorer one-click restore: preselect exactly the requested object
      document.querySelectorAll('#r_items .r-sel').forEach(b => {
        const it = _restoreItems[+b.dataset.i];
        b.checked = !!it && it.resource_type === _restorePreselect.rt && String(it.object_id) === _restorePreselect.oid;
      });
      _restorePreselect = null;
    }
    updateRestoreCount();
  } catch(e){ document.getElementById('r_summary').textContent = 'Preview failed: '+e.message; }
}
async function restoreApply(){
  const tgt = parseInt(document.getElementById('r_target').value);
  const promoting = tgt!==_restoreCtx.tenantId;
  const boxes=[...document.querySelectorAll('#r_items .r-sel')];
  const checked=boxes.filter(b=>b.checked);
  if(boxes.length && checked.length===0){ toast('Select at least one item to restore', true); return; }
  const partial = boxes.length && checked.length<boxes.length;
  if(!confirm(promoting ? 'PROMOTE: apply this snapshot into a DIFFERENT tenant? This writes objects into that target tenant.' : `Apply this restore? It writes the ${partial?checked.length+' selected':'changed'} object(s) back into the live tenant. This cannot be auto-undone.`)) return;
  document.getElementById('r_summary').textContent = 'Applying…';
  document.getElementById('r_applybtn').disabled = true;
  try {
    const payload = {snapshot_ts:_restoreCtx.snap};
    if(tgt!==_restoreCtx.tenantId) payload.target_tenant_id = tgt;
    if(partial){
      payload.selection = {objects: checked.map(b=>{const it=_restoreItems[+b.dataset.i]; return {resource_type:it.resource_type, object_id:it.object_id};})};
    }
    const res = await api(`/tenants/${_restoreCtx.tenantId}/restore/apply`, {method:'POST', body: JSON.stringify(payload)});
    renderRestore(res);
    toast('Restore applied - see the report below.');
    document.getElementById('r_applybtn').classList.add('hidden');
  } catch(e){ document.getElementById('r_summary').textContent = 'Apply failed: '+e.message; }
  document.getElementById('r_applybtn').disabled = false;
}


/* ---------- snapshot browser ---------- */
let _browse = null;
async function openBrowse(ts){
  _browse = { tenantId: snapTenantId, snap: ts, type: null };
  document.getElementById('b_label').textContent = `${window._snapSlug} @ ${fmtSnap(ts)}`;
  document.getElementById('b_search').value = '';
  document.getElementById('b_objects').innerHTML = '';
  document.getElementById('browsepanel').classList.remove('hidden');
  try {
    const d = await api(`/tenants/${_browse.tenantId}/snapshots/${ts}/objects`);
    document.getElementById('b_types').innerHTML = d.types.map(t=>
      `<label onclick="browseType('${t.resource_type}')"><input type="radio" name="btype"> ${t.resource_type} (${t.count})</label>`).join('');
  } catch(e){ toast(e.message, true); }
}
function browseType(rt){ _browse.type = rt; browseObjects(); }
async function browseObjects(){
  if(!_browse || !_browse.type) return;
  const q = document.getElementById('b_search').value.trim();
  const tb = document.getElementById('b_objects');
  tb.innerHTML = skelRows(3);
  try {
    const d = await api(`/tenants/${_browse.tenantId}/snapshots/${_browse.snap}/objects?resource_type=${encodeURIComponent(_browse.type)}${q?'&q='+encodeURIComponent(q):''}`);
    if(!d.objects.length){ tb.innerHTML=emptyRow(3, EI.search, 'No matching objects.'); return; }
    tb.innerHTML = d.objects.map(o=>`<tr>
      <td>${esc(o.object_name||'-')}</td><td class="muted">${esc(o.object_id)}</td>
      <td><button onclick="viewObject('${esc(o.object_id)}')">View</button></td></tr>`).join('');
  } catch(e){ tb.innerHTML=`<tr><td colspan="3" class="muted">${esc(e.message)}</td></tr>`; }
}
async function viewObject(oid){
  try {
    const d = await api(`/tenants/${_browse.tenantId}/snapshots/${_browse.snap}/objects/${encodeURIComponent(_browse.type)}/${encodeURIComponent(oid)}`);
    document.getElementById('difflabel').textContent = `${_browse.type} / ${oid}`;
    document.getElementById('diffstat').innerHTML = '';
    document.getElementById('difftablewrap').classList.add('hidden');
    document.getElementById('diffout').classList.remove('hidden');
    document.getElementById('diffout').textContent = JSON.stringify(d.object, null, 2);
    document.getElementById('diffpanel').classList.remove('hidden');
    document.getElementById('diffpanel').scrollIntoView({behavior:'smooth'});
  } catch(e){ toast(e.message, true); }
}


/* ---------- identity ---------- */
let _idCtx = null;
function openIdentity(id, slug){
  const t = _tenants.find(x=>x.id===id);
  _idCtx = {tenantId:id, slug, provider: t ? t.provider : null};
  // Authentik has no direct app assignments; access rides policy bindings, so the
  // column shows the (real) bindings count instead of a structurally-zero number.
  const th = document.getElementById('id_asg_th');
  if(th) th.textContent = _idCtx.provider === 'authentik' ? 'Policy bindings' : 'Assignments';
  document.getElementById('id_slug').textContent = slug;
  document.getElementById('idpanel').classList.remove('hidden');
  document.getElementById('idpanel').scrollIntoView({behavior:'smooth'});
  loadIdentityEstimate(); loadIdentitySnaps();
}
async function loadIdentityEstimate(){
  try{
    const e = await api(`/tenants/${_idCtx.tenantId}/identity/estimate`);
    document.getElementById('id_estimate').innerHTML = e.last_duration_s!=null
      ? `Last run: <b>${e.last_duration_s}s</b>, ${e.last_api_calls} API calls (${esc(e.basis)}). ${esc(e.recommendation)}`
      : `No measured run yet. ${esc(e.recommendation)}`;
  }catch(e){ document.getElementById('id_estimate').textContent=''; }
}
let selectedIdSnaps = [];
async function loadIdentitySnaps(){
  const tb = document.getElementById('id_snaps');
  selectedIdSnaps = []; updateIdSnapButtons();
  tb.innerHTML = skelRows(8);
  try{
    const s = await api(`/tenants/${_idCtx.tenantId}/identity/snapshots`);
    if(!s.length){ tb.innerHTML=emptyRow(8, EI.users, 'No Users & Access snapshots yet - enable Users & Access backup on the tenant (Edit) and run one.'); return; }
    tb.innerHTML = s.map(r=>{
      const c = r.counts||{};
      const box = `<td><input type="checkbox" class="idsel" data-ts="${r.ts}" tabindex="-1" onchange="selIdSnap(this)"></td>`;
      const asg = _idCtx.provider === 'authentik'
        ? (c.app_policy_bindings != null ? c.app_policy_bindings : '-')
        : (c.app_group_assignments||0)+(c.app_user_assignments_direct||0);
      return r.status==='failed'
        ? `<tr>${box}<td>${fmtSnap(r.ts)}</td><td colspan="5" class="st-failed">failed: ${esc(r.error||'')}</td><td></td></tr>`
        : `<tr>${box}<td>${fmtSnap(r.ts)}</td><td>${c.users||0}</td><td>${c.group_memberships||0}</td><td>${asg}</td><td class="muted">${r.duration_ms?Math.round(r.duration_ms/1000)+'s':'-'}</td><td class="muted">${r.api_calls||'-'}</td><td><button onclick="openIdentityRestore('${r.ts}')">Restore…</button></td></tr>`;
    }).join('');
  }catch(e){ tb.innerHTML=`<tr><td colspan="8" class="muted">${esc(e.message)}</td></tr>`; }
}
function selIdSnap(cb){
  const ts = cb.dataset.ts;
  if(cb.checked){ if(!selectedIdSnaps.includes(ts)) selectedIdSnaps.push(ts); }
  else selectedIdSnaps = selectedIdSnaps.filter(x=>x!==ts);
  updateIdSnapButtons();
}
function selAllIdSnaps(on){
  selectedIdSnaps = [];
  document.querySelectorAll('#id_snaps .idsel').forEach(cb => { cb.checked = on; if(on) selectedIdSnaps.push(cb.dataset.ts); });
  updateIdSnapButtons();
}
function updateIdSnapButtons(){
  const d = document.getElementById('delidsnapsbtn');
  if(d){ d.classList.toggle('hidden', me.role !== 'admin' || selectedIdSnaps.length === 0);
    d.textContent = `Delete selected (${selectedIdSnaps.length})`; }
  const all = document.getElementById('idsnapall');
  if(all){ const rows = document.querySelectorAll('#id_snaps .idsel').length;
    all.checked = rows > 0 && selectedIdSnaps.length === rows; }
}
function deleteIdentitySnapshots(){
  const n = selectedIdSnaps.length; if(!n) return;
  askDelete(`Permanently delete ${n} selected Users & Access backup(s) for "${_idCtx.slug}"? This cannot be undone.`, async pw => {
    try{
      const r = await api(`/tenants/${_idCtx.tenantId}/identity/snapshots/delete`, {method:'POST', body: JSON.stringify({timestamps: selectedIdSnaps, password: pw})});
      toast(`${r.deleted.length} Users & Access backup(s) deleted.`);
      loadIdentitySnaps();
    }catch(e){ toast('Delete failed: ' + e.message, true); }
  });
}
async function identityBackupNow(){
  document.getElementById('id_estimate').textContent = 'Backing up users & access… (throttled to respect Okta API limits - may take a while on large orgs)';
  try{
    const r = await api(`/tenants/${_idCtx.tenantId}/identity/backup`, {method:'POST'});
    toast(`Users & Access backup done - ${r.api_calls} API calls in ${Math.round(r.duration_ms/1000)}s.`);
    loadIdentityEstimate(); loadIdentitySnaps();
  }catch(e){ toast('Users & Access backup failed: '+e.message, true); loadIdentityEstimate(); }
}
let _irCtx = null;
const RECREATE_SELECT_MAX = 200;
const IR_HEAD = `<div class="restore-item" style="font-weight:600;border-bottom:1px solid var(--border)"><div></div><div>ACTION</div><div>OBJECT</div><div>STATUS</div></div>`;
function openIdentityRestore(ts){
  _irCtx = { ts, preview: null };
  document.getElementById('ir_tenant').textContent = document.getElementById('id_slug').textContent;
  document.getElementById('ir_snap').textContent = fmtSnap(ts);
  document.getElementById('ir_summary').textContent = '';
  document.getElementById('ir_items').innerHTML = '';
  document.getElementById('ir_applybtn').classList.add('hidden');
  document.getElementById('idrestoremodal').classList.remove('hidden');
}
function closeIdentityRestore(){ document.getElementById('idrestoremodal').classList.add('hidden'); _irCtx=null; }
async function identityPreview(){
  document.getElementById('ir_summary').textContent = 'Running preview… (read-only)';
  try{
    const p = await api(`/tenants/${_idCtx.tenantId}/identity/restore/preview`, {method:'POST', body: JSON.stringify({snapshot_ts:_irCtx.ts})});
    _irCtx.preview = p;
    renderIdentityRestore(p);
  }catch(e){ document.getElementById('ir_summary').textContent = 'Preview failed: '+e.message; }
}
function renderIdentityRestore(p){
  const u = p.summary.users;
  const gm = p.summary.group_memberships_to_add||0;
  const ag = p.summary.app_group_assignments_to_add||0;
  const au = p.summary.app_user_assignments_direct_to_add||0;
  document.getElementById('ir_summary').innerHTML =
    `<b>Preview:</b> <span class="ev-add">${u.recreate} recreate</span> · <span class="muted">${u.identical} identical</span>` +
    (u.update>0?` · <span class="muted">${u.update} differ (create-only: left untouched)</span>`:'') +
    (_idApplyable(p)?'':' · <span class="muted">nothing to apply - live already matches this snapshot</span>');
  const list = p.recreate_users || [];
  const selectable = list.length>0 && list.length<=RECREATE_SELECT_MAX && !p.recreate_truncated;
  const selbar = selectable ? `<div style="margin:8px 0 4px"><button onclick="setAllIdentity(true)">Select all</button> <button onclick="setAllIdentity(false)">Unselect all</button></div>` : '';
  const userRows = selectable
    ? list.map(x=>`<div class="restore-item">
        <div><input type="checkbox" class="ir-sel" value="${esc(x.key)}" checked onchange="updateIdentityCount()"></div>
        <div class="act-create">recreate</div>
        <div>user / ${esc(x.label||x.key)}${x.email&&x.email!==(x.label||x.key)?` <span class="muted">${esc(x.email)}</span>`:''}</div>
        <div class="st-planned">missing in live</div></div>`).join('')
    : (list.length ? `<div class="restore-item"><div></div><div class="act-create">recreate</div><div>${list.length}${p.recreate_truncated?'+':''} users - too many to select individually; Apply restores all</div><div class="st-planned">missing in live</div></div>` : '');
  const agg = [];
  if(gm) agg.push(`<div class="restore-item"><div></div><div class="act-create">add</div><div>group memberships</div><div class="ev-add">+${gm} to re-add</div></div>`);
  if(ag) agg.push(`<div class="restore-item"><div></div><div class="act-create">add</div><div>app assignments (via group)</div><div class="ev-add">+${ag} to re-add</div></div>`);
  if(au) agg.push(`<div class="restore-item"><div></div><div class="act-create">add</div><div>app assignments (direct user)</div><div class="ev-add">+${au} to re-add</div></div>`);
  const extra = (p.manual_steps&&p.manual_steps.length?`<div style="margin-top:10px"><b>Manual steps after restore:</b><ul style="margin:6px 0 0 18px">${p.manual_steps.map(m=>`<li>${esc(m)}</li>`).join('')}</ul></div>`:'')
    + (p.note?`<p class="muted" style="font-size:.8rem;margin-top:8px">${esc(p.note)}</p>`:'');
  document.getElementById('ir_items').innerHTML = selbar + IR_HEAD + userRows + agg.join('') + extra;
  updateIdentityCount();
}
function setAllIdentity(v){ document.querySelectorAll('#ir_items .ir-sel').forEach(b=>b.checked=v); updateIdentityCount(); }
function updateIdentityCount(){
  const p = _irCtx && _irCtx.preview; if(!p) return;
  const btn = document.getElementById('ir_applybtn');
  if(!_idApplyable(p)){ btn.classList.add('hidden'); return; }
  btn.classList.remove('hidden'); btn.disabled = false;
  const boxes = [...document.querySelectorAll('#ir_items .ir-sel')];
  if(boxes.length){
    const n = boxes.filter(b=>b.checked).length;
    btn.textContent = `Apply restore (${n} user${n===1?'':'s'})`;
  } else btn.textContent = 'Apply restore';
}

function _idApplyable(p){
  const u = p.summary.users;
  return (u.recreate||0) + (p.summary.group_memberships_to_add||0)
       + (p.summary.app_group_assignments_to_add||0) + (p.summary.app_user_assignments_direct_to_add||0) > 0;
}
async function identityApply(){
  const p = _irCtx && _irCtx.preview; if(!p) return;
  let selection = null;
  const boxes = [...document.querySelectorAll('#ir_items .ir-sel')];
  if(boxes.length){
    selection = boxes.filter(b=>b.checked).map(b=>b.value);
    if(selection.length===0 && !confirm('No users selected to recreate. Continue anyway (memberships/assignments only)?')) return;
  }
  const n = selection ? selection.length : (p.summary.users.recreate||0);
  if(!confirm(`APPLY Users & Access restore? This WRITES to the live tenant: recreates ${n} user(s) and re-adds missing memberships/assignments. Recreated users will need password + MFA reset. Continue?`)) return;
  document.getElementById('ir_summary').textContent = 'Applying… (throttled; may take a while)';
  document.getElementById('ir_applybtn').disabled = true;
  try{
    const payload = {snapshot_ts:_irCtx.ts, confirm:true}; if(selection) payload.selection = selection;
    const r = await api(`/tenants/${_idCtx.tenantId}/identity/restore/apply`, {method:'POST', body: JSON.stringify(payload)});
    const rep = r.summary;
    const row = (cat) => { const c=rep[cat]||{};
      const done = [c.created!=null?c.created+' created':null, c.added!=null?c.added+' added':null,
                    c.existing!=null?c.existing+' existing':null, c.skipped!=null?c.skipped+' skipped':null]
                   .filter(Boolean).join(' · ');
      return `<div class="restore-item"><div></div><div class="act-create">${cat.replace(/_/g,' ')}</div><div>${done||'-'}</div><div>${c.failed?`<span class="st-failed">${c.failed} failed</span>`:'<span class="st-created">ok</span>'}</div></div>`; };
    document.getElementById('ir_summary').innerHTML = `<b>Applied:</b> snapshot ${fmtSnap(_irCtx.ts)}`;
    document.getElementById('ir_items').innerHTML = IR_HEAD +
      ['users','group_memberships','app_group_assignments','app_user_assignments_direct'].map(row).join('') +
      (r.manual_steps.length?`<div style="margin-top:10px"><b>Do these now:</b><ul style="margin:6px 0 0 18px">${r.manual_steps.map(m=>`<li>${esc(m)}</li>`).join('')}</ul></div>`:'');
    document.getElementById('ir_applybtn').classList.add('hidden');
    toast('Users & Access restore applied - see the report.');
    loadIdentitySnaps();
  }catch(e){ document.getElementById('ir_summary').textContent = 'Apply failed: '+e.message; }
  document.getElementById('ir_applybtn').disabled = false;
}



/* ---------- explorer (v1.2 Phase 2) ---------- */
let _ex = null;
let _restorePreselect = null;
async function openExplorer(t){
  _ex = { tenantId: t.id, slug: t.slug, snap: null, cat: null, isLatest: true };
  snapTenantId = t.id; window._snapSlug = t.slug;
  document.getElementById('ex_objpanel').classList.add('hidden');
  document.getElementById('ex_detailpanel').classList.add('hidden');
  const sel = document.getElementById('ex_snap');
  sel.innerHTML = '';
  document.getElementById('ex_cats').innerHTML = '<div class="card"><div class="sub">Loading…</div></div>';
  try{
    const snaps = await api(`/tenants/${t.id}/snapshots`);
    if(!snaps.length){ document.getElementById('ex_cats').innerHTML = '<div class="card"><div class="lbl2">No snapshots yet</div><div class="sub">Run a backup, then explore it here.</div></div>'; return; }
    const rev = snaps.slice().reverse();
    sel.innerHTML = '<option value="current">Current (live from provider)</option>' +
      rev.map((s,i)=>`<option value="${s.ts}">${i===0?'Latest backup - ':''}${fmtSnap(s.ts)}</option>`).join('');
    _ex.snap = window._exJumpSnap || rev[0].ts;
    sel.value = _ex.snap;
    window._exJumpSnap = null;
    await exLoadCats();
    if(window._exJumpCat){ exOpenCat(window._exJumpCat); window._exJumpCat = null; }
  }catch(e){ document.getElementById('ex_cats').innerHTML = `<div class="card bad"><div class="sub">${esc(e.message)}</div></div>`; }
}
async function exLoadCats(){
  const sel = document.getElementById('ex_snap');
  if(sel.value) _ex.snap = sel.value;
  document.getElementById('ex_objpanel').classList.add('hidden');
  document.getElementById('ex_detailpanel').classList.add('hidden');
  const grid = document.getElementById('ex_cats');
  grid.innerHTML = '<div class="card"><div class="sub">Loading…</div></div>';
  try{
    const d = await api(`/tenants/${_ex.tenantId}/snapshots/${_ex.snap}/explore`);
    _ex.mode = d.mode; _ex.isLatest = d.mode === 'snapshot' && d.is_latest; _ex.latest = d.latest;
    grid.innerHTML = d.categories.map(c=>{
      const delta = _ex.mode === 'current' ? c.count - c.current_count : c.current_count - c.count;
      const badge = _ex.isLatest || delta === 0 ? ''
        : ` <span style="font-size:.75rem;color:${delta>0?'var(--green)':'var(--red)'}">${delta>0?'+'+delta:delta}${_ex.mode==='current'?' vs backup':' in latest'}</span>`;
      const sub = _ex.mode === 'current' ? c.current_count + ' in the latest backup'
        : _ex.isLatest ? 'objects in the latest backup' : c.current_count + ' in the latest backup';
      return `<div class="card" style="cursor:pointer" onclick="exOpenCat('${esc(c.resource_type)}')">
        <div class="lbl2">${esc(c.resource_type.replace(/_/g,' '))}</div>
        <div class="big">${c.count}${badge}</div>
        <div class="sub">${sub}</div></div>`;
    }).join('');
  }catch(e){ grid.innerHTML = `<div class="card bad"><div class="sub">${esc(e.message)}</div></div>`; }
}
function exOpenCat(rt){
  _ex.cat = rt;
  document.getElementById('ex_cat_label').textContent = rt.replace(/_/g,' ');
  document.getElementById('ex_search').value = '';
  document.getElementById('ex_objpanel').classList.remove('hidden');
  document.getElementById('ex_detailpanel').classList.add('hidden');
  exLoadObjects();
  document.getElementById('ex_objpanel').scrollIntoView({behavior:'smooth'});
}
const EX_STATUS = {
  unchanged: '<span class="tag" style="background:var(--tag-dim-bg);color:var(--dim)">unchanged</span>',
  modified: '<span class="tag pending">modified</span>',
  deleted: '<span class="tag off">deleted in latest</span>',
  new: '<span class="tag ok">new in latest</span>',
};
const EX_STATUS_CURRENT = {
  unchanged: '<span class="tag" style="background:var(--tag-dim-bg);color:var(--dim)">backed up</span>',
  modified: '<span class="tag pending">changed since backup</span>',
  deleted: '<span class="tag off">deleted since backup</span>',
  new: '<span class="tag pending">not backed up yet</span>',
};
function exStatusTag(status){
  const map = _ex && _ex.mode === 'current' ? EX_STATUS_CURRENT : EX_STATUS;
  return map[status] || esc(status);
}
async function exLoadObjects(){
  if(!_ex || !_ex.cat) return;
  const q = v('ex_search');
  const tb = document.getElementById('ex_objects');
  tb.innerHTML = skelRows(4);
  try{
    const d = await api(`/tenants/${_ex.tenantId}/snapshots/${_ex.snap}/explore?resource_type=${encodeURIComponent(_ex.cat)}${q?'&q='+encodeURIComponent(q):''}`);
    if(!d.objects.length){ tb.innerHTML = emptyRow(4, EI.search, 'No matching objects.'); return; }
    const canW = me.role === 'admin' || me.role === 'org_admin';
    const inactive = _tenants.find(x=>x.id===_ex.tenantId)?.active === false;
    tb.innerHTML = d.objects.map(o=>`<tr>
      <td>${esc(o.object_name||'-')}</td><td class="muted" style="font-size:.78rem">${esc(o.object_id)}</td>
      <td>${_ex.isLatest ? '<span class="muted">-</span>' : exStatusTag(o.status)}</td>
      <td style="white-space:nowrap"><button onclick="exViewObject('${esc(o.object_id)}')">View</button>
      ${canW && !inactive && o.status !== 'new' ? ` <button onclick="exRestoreObject('${esc(o.object_id)}')" title="Preview restoring this object from this snapshot (dry-run first, nothing is written until you apply)">Restore… ${TIPI}</button>` : ''}</td></tr>`).join('');
  }catch(e){ tb.innerHTML = `<tr><td colspan="4" class="muted">${esc(e.message)}</td></tr>`; }
}
async function exViewObject(oid){
  try{
    const d = await api(`/tenants/${_ex.tenantId}/snapshots/${_ex.snap}/explore/${encodeURIComponent(_ex.cat)}/${encodeURIComponent(oid)}`);
    document.getElementById('ex_obj_label').textContent = `${_ex.cat.replace(/_/g,' ')} / ${oid}`;
    document.getElementById('ex_col_a').textContent = _ex.mode === 'current' ? 'Current (live)' : 'In this snapshot';
    document.getElementById('ex_col_b').textContent = 'In latest backup';
    document.getElementById('ex_snapjson').textContent = d.object ? JSON.stringify(d.object, null, 2) : '(not in this snapshot)';
    document.getElementById('ex_curjson').textContent = d.current ? JSON.stringify(d.current, null, 2) : '(deleted - not in the latest backup)';
    document.getElementById('ex_diffinfo').innerHTML = d.status === 'modified' && d.changed_fields.length
      ? `Changed fields: <b>${d.changed_fields.map(esc).join(', ')}</b>`
      : (_ex.isLatest ? '' : exStatusTag(d.status));
    document.getElementById('ex_detailpanel').classList.remove('hidden');
    document.getElementById('ex_detailpanel').scrollIntoView({behavior:'smooth'});
  }catch(e){ toast(e.message, true); }
}
function exRestoreObject(oid){
  const snap = _ex.mode === 'current' ? _ex.latest : _ex.snap;
  if(!snap) return toast('No backup to restore from yet.', true);
  _restorePreselect = { rt: _ex.cat, oid: String(oid) };
  openRestore(snap);
  restorePreview();
}