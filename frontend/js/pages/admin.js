/* IdPVault - pages/admin.js: app users, client orgs (MSP), license, settings, audit, events, docs. Split from index.html (v1.2 Phase 1a). */
async function resetUserPw(id, name){
  if(!confirm(`Send a password reset for "${name}"? A one-time reset link is generated (emailed if the user has an email + SMTP is configured).`)) return;
  try{
    const r = await api(`/users/${id}/reset`, {method:'POST'});
    const link = location.origin + r.reset_link;
    toast(r.emailed ? `Reset link emailed to ${name}. Link (backup): ${link}` : `Reset link for ${name}: ${link}`);
    try{ await navigator.clipboard.writeText(link); }catch{}
  }catch(e){ toast(e.message, true); }
}
async function resetUserMfa(id, name){
  if(!confirm(`Reset MFA for "${name}"? Their two-factor is removed and trusted devices are revoked - they can set it up again from their profile.`)) return;
  try{ await api(`/users/${id}/reset-mfa`, {method:'POST'}); toast(`MFA reset for ${name}.`); loadUsers(); }
  catch(e){ toast(e.message, true); }
}
/* ---------- users admin ---------- */
async function loadUsers(){
  const ub = document.getElementById('userbody');
  ub.innerHTML = skelRows(5);
  try {
    const us = await api('/users');
    const msp = (me.features||[]).includes('msp');
    if(!msp) document.querySelectorAll("#u_role option[value='org_admin'],#u_role option[value='org_viewer']").forEach(o=>o.remove());
    if(msp){ try { _orgs = await api('/orgs'); } catch {} }
    try { _license = await api('/license'); } catch {}
    const atUserCap = _license && _license.max_users != null && us.length >= _license.max_users;
    const ab = document.getElementById('adduserbtn');
    if(ab){ ab.disabled = !!atUserCap;
      ab.title = atUserCap ? 'User limit reached for your license - the free Community tier includes a single admin account. Add a license in Administration > License' : '';
      ab.innerHTML = '+ Add user' + (atUserCap ? ' ' + TIPI : '');
      if(atUserCap) document.getElementById('userform').classList.add('hidden'); }
    ub.innerHTML = us.map(u => `<tr>
      <td>${esc(u.username)}${u.username===me.username?' <span class="muted">(you)</span>':''}</td>
      <td class="muted">${esc(u.email||'-')}</td>
      <td><span class="tag ${u.role}">${u.role}</span>${u.org_name?` <span class="muted" style="font-size:.72rem">${esc(u.org_name)}</span>`:''}</td>
      <td>${u.pending_invite ? '<span class="tag pending">invite pending</span>' : u.is_active ? '<span class="tag ok">active</span>' : '<span class="tag off">disabled</span>'}</td>
      <td style="white-space:nowrap">${u.username===me.username ? '<span class="muted">-</span>' : `
        <button onclick="patchUser(${u.id}, {role: '${u.role==='admin'?'user':'admin'}'})">Make ${u.role==='admin'?'user':'admin'}</button>
        <button onclick="patchUser(${u.id}, {is_active: ${!u.is_active}})">${u.is_active?'Disable':'Enable'}</button>
        <button onclick="resetUserPw(${u.id}, '${esc(u.username)}')">Reset password</button>
        <button onclick="resetUserMfa(${u.id}, '${esc(u.username)}')">Reset MFA</button>
        <button class="del" onclick="delUser(${u.id}, '${esc(u.username)}')">Delete</button>`}
      </td></tr>`).join('');
  } catch(e){ ub.innerHTML = `<tr><td colspan="5" class="muted">${esc(e.message)}</td></tr>`; }
}
async function createUser(){
  try {
    const body = {username: v('u_name'), email: v('u_email'), role: v('u_role')};
    if(body.role==='org_admin' || body.role==='org_viewer'){
      if(!v('u_org')) return toast('Org-scoped roles need a client org - create one on the Orgs page first.', true);
      body.org_id = parseInt(v('u_org'));
    }
    if(v('u_mode')==='password'){
      if((v('u_pw')||'').length < 8) return toast('Password must be at least 8 characters.', true);
      body.password = v('u_pw');
    }
    const r = await api('/users', {method:'POST', body: JSON.stringify(body)});
    if(r.invite_link){
      const link = location.origin + r.invite_link;
      toast(r.emailed ? `Invite emailed. Link (backup): ${link}` : `User created. Invite link: ${link}`);
      try { await navigator.clipboard.writeText(link); } catch {}
    } else {
      toast('User created and active - they can sign in now.');
    }
    ['u_name','u_email','u_pw'].forEach(i=>document.getElementById(i).value='');
    loadUsers();
  } catch(e){ toast('Create failed: '+e.message, true); }
}
async function patchUser(id, body){
  try { await api(`/users/${id}`, {method:'PATCH', body: JSON.stringify(body)}); loadUsers(); }
  catch(e){ toast(e.message, true); }
}
async function delUser(id, name){
  if(!confirm(`Delete user "${name}"?`)) return;
  try { await api(`/users/${id}`, {method:'DELETE'}); toast(`User "${name}" deleted.`); loadUsers(); }
  catch(e){ toast(e.message, true); }
}

/* ---------- client orgs (MSP) ---------- */
function onUserRoleChange(){
  const r = v('u_role'), isOrg = r==='org_admin' || r==='org_viewer';
  const d = document.getElementById('ud_org'); if(!d) return;
  d.classList.toggle('hidden', !isOrg);
  if(isOrg) document.getElementById('u_org').innerHTML =
    _orgs.length ? _orgs.map(o=>`<option value="${o.id}">${esc(o.name)}</option>`).join('')
                 : '<option value="">no orgs yet - create one on the Orgs page</option>';
}
async function loadOrgs(){
  const ob = document.getElementById('orgbody');
  ob.innerHTML = skelRows(7);
  try {
    _orgs = await api('/orgs');
    if(!_orgs.length){ ob.innerHTML = emptyRow(7, EI.db, 'No client orgs yet - add one to group tenants and users per client.'); return; }
    ob.innerHTML = _orgs.map(o=>`<tr>
      <td>${esc(o.name)}${o.notes?` <span class="tipi" title="${esc(o.notes)}">ⓘ</span>`:''}</td>
      <td class="muted">${esc(o.contact_name||'-')}${o.contact_email?`<br><span style="font-size:.75rem">${esc(o.contact_email)}</span>`:''}${o.contact_phone?`<br><span style="font-size:.75rem">${esc(o.contact_phone)}</span>`:''}</td>
      <td>${o.tenant_count}</td><td>${o.user_count}</td>
      <td class="muted">${esc(o.billing_memo||'-')}${o.billing_cadence?` · ${o.billing_cadence}`:''}</td>
      <td>${o.renewal_date?fmtUS(o.renewal_date):'<span class="muted">-</span>'}</td>
      <td style="white-space:nowrap"><button onclick="editOrg(${o.id})">Edit</button> <button class="del" onclick="delOrg(${o.id}, '${esc(o.name)}')">Delete</button></td></tr>`).join('');
  } catch(e){ ob.innerHTML = `<tr><td colspan="7" class="muted">${esc(e.message)}</td></tr>`; }
}
function toggleOrgForm(){
  const f = document.getElementById('orgform'); f.classList.toggle('hidden');
  if(f.classList.contains('hidden')){
    _editOrgId = null;
    ['o_name','o_cname','o_cemail','o_cphone','o_bmemo','o_renew','o_notes'].forEach(i=>document.getElementById(i).value='');
    document.getElementById('o_bcad').value=''; document.getElementById('orgformtitle').textContent='';
  }
}
function editOrg(id){
  const o = _orgs.find(x=>x.id===id); if(!o) return;
  _editOrgId = id;
  document.getElementById('orgform').classList.remove('hidden');
  document.getElementById('orgformtitle').textContent = `Editing "${o.name}"`;
  document.getElementById('o_name').value = o.name;
  document.getElementById('o_cname').value = o.contact_name||'';
  document.getElementById('o_cemail').value = o.contact_email||'';
  document.getElementById('o_cphone').value = o.contact_phone||'';
  document.getElementById('o_bmemo').value = o.billing_memo||'';
  document.getElementById('o_bcad').value = o.billing_cadence||'';
  document.getElementById('o_renew').value = o.renewal_date||'';
  document.getElementById('o_notes').value = o.notes||'';
}
async function saveOrg(){
  const body = { name: v('o_name'), contact_name: v('o_cname'), contact_email: v('o_cemail'),
    contact_phone: v('o_cphone'), billing_memo: v('o_bmemo'), billing_cadence: v('o_bcad'),
    renewal_date: v('o_renew'), notes: v('o_notes') };
  if(!body.name) return toast('Org name is required', true);
  try {
    if(_editOrgId) await api(`/orgs/${_editOrgId}`, {method:'PATCH', body: JSON.stringify(body)});
    else await api('/orgs', {method:'POST', body: JSON.stringify(body)});
    toast(_editOrgId ? 'Org updated.' : `Org "${body.name}" created.`);
    toggleOrgForm(); loadOrgs();
  } catch(e){ toast('Save failed: '+e.message, true); }
}
async function delOrg(id, name){
  if(!confirm(`Delete org "${name}"? Its tenants and users are kept but unassigned.`)) return;
  try { await api(`/orgs/${id}`, {method:'DELETE'}); toast(`Org "${name}" deleted.`); loadOrgs(); }
  catch(e){ toast(e.message, true); }
}
async function _dlCsv(path, name){
  try {
    const r = await fetch('/api/v1'+path);
    if(!r.ok){ const b = await r.json().catch(()=>({})); throw new Error(b.detail || 'HTTP '+r.status); }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob()); a.download = name;
    a.click(); URL.revokeObjectURL(a.href);
  } catch(e){ toast('Download failed: '+e.message, true); }
}
function exportOrgs(){ _dlCsv('/orgs/export', 'idpvault-orgs.csv'); }
function orgTemplate(){ _dlCsv('/orgs/template', 'idpvault-orgs-template.csv'); }
async function importOrgsFile(inp){
  const f = inp.files[0]; inp.value = ''; if(!f) return;
  if(f.size > 1000000) return toast('CSV too large (1 MB max)', true);
  const text = await f.text();
  try {
    const r = await api('/orgs/import', {method:'POST', body: JSON.stringify({csv: text})});
    let msg = `${r.imported} org(s) imported`;
    if(r.skipped.length) msg += `, ${r.skipped.length} skipped (name already exists)`;
    if(r.errors.length) msg += `. ${r.errors.slice(0,3).join('; ')}` + (r.errors.length>3 ? ` (+${r.errors.length-3} more)` : '');
    toast(msg, r.errors.length > 0);
    if(r.imported) loadOrgs();
  } catch(e){ toast('Import failed: '+e.message, true); }
}
/* ---------- settings ---------- */
// Grouped UI over the backend alert categories. The backend categories are
// unchanged; each group expands to its category list on save.
const ALERT_GROUPS = [
  {key:'cfg',      label:'Config Backups',          when:'changes detected during a config backup (change list included in the message), config backup failures, and the overdue-backup watchdog'},
  {key:'ua',       label:'Users & Access Backups',  when:'users, memberships, or assignments changed vs the previous Users & Access snapshot (change list included), and Users & Access backup failures'},
  {key:'restores', label:'Restores',                when:'a config or Users & Access restore is written to a live tenant'},
  {key:'success',  label:'Successful backups too',  when:'also alert when a backup completes with NO changes (noisy; off by default). Applies to whichever backup types are checked above, for this channel.'},
];
function _alertGroupsFromCats(list){
  return {cfg: list.includes('drift_detected'),
          ua: list.includes('identity_drift'),
          restores: list.includes('restore_applied'),
          success: list.includes('backup_success') || list.includes('identity_backup_success')};
}
function _alertCatsFromGroups(g){
  const cats = [];
  if(g.cfg){ cats.push('drift_detected', 'backup_failed', 'backup_stale'); if(g.success) cats.push('backup_success'); }
  if(g.ua){ cats.push('identity_drift'); if(!cats.includes('backup_failed')) cats.push('backup_failed'); if(g.success) cats.push('identity_backup_success'); }
  if(g.restores) cats.push('restore_applied');
  return cats;
}
function _alertGroupChecks(id){
  const g = {};
  document.querySelectorAll(`#${id} input`).forEach(b => g[b.value] = b.checked);
  return g;
}
function renderAlertEvents(enabledEmail, enabledWebhook){
  const grp = (id, cats) => {
    const g = _alertGroupsFromCats(cats);
    document.getElementById(id).innerHTML = ALERT_GROUPS.map(e =>
      `<label><input type="checkbox" value="${e.key}" ${g[e.key]?'checked':''}> ${esc(e.label)} <span class="tipi" title="${esc(e.when)}">ⓘ</span></label>`).join('');
  };
  grp('s_events_email', enabledEmail);
  grp('s_events_webhook', enabledWebhook);
}
/* ---------- license ---------- */
async function loadLicense(){
  const box = document.getElementById('lic_status'); if(!box) return;
  try { _license = await api('/license'); } catch(e){ box.textContent = 'Failed to load license: ' + e.message; return; }
  const l = _license;
  document.getElementById('lic_clear').classList.toggle('hidden', !(l.valid || l.invalid_present));
  if(!l.valid){
    const bad = l.invalid_present ? '<span class="st-failed">The installed license key is invalid or fully expired - paid features are paused.</span><br>' : '';
    box.innerHTML = bad + `<b>Community tier</b> (free) - ${l.tenant_count}/${l.max_tenants} tenant · ${l.user_count}/${l.max_users} user · config backup &amp; restore included. `
      + `A paid license unlocks more tenants, more users, and Users &amp; Access backup &amp; restore. <a href="https://idpvault.com" target="_blank" rel="noopener">Get a license at idpvault.com</a>`;
    return;
  }
  const expDate = l.expires ? new Date(l.expires*1000).toISOString().slice(0,10) : 'never';
  const daysToExp = l.expires ? Math.max(0, Math.floor((l.expires*1000 - Date.now())/86400000)) : null;
  const grace = l.status==='grace'
    ? ` <span class="st-failed">- EXPIRED: in the ${l.grace_days}-day grace window, ${l.days_left} day(s) before paid features pause. Install your renewal key now.</span>` : '';
  box.innerHTML = `<b class="tiername">${esc(l.tier)}</b> license - ${esc(l.customer||'')} · expires <b>${expDate}</b>`
    + (daysToExp!=null && l.status==='active' ? ` <span class="muted">(${daysToExp} days from now)</span>` : '') + grace
    + `<br>Tenants: ${l.tenant_count} / ${l.max_tenants==null?'unlimited':l.max_tenants} · Users: ${l.user_count} / ${l.max_users==null?'unlimited':l.max_users} · Features: ${esc((l.features||[]).join(', ')||'-')}`
    + `<br><span class="muted" style="font-size:.78rem">Renewal keys can be installed any time before expiry - the new key's term extends from the old expiry date, so renewing early never loses time.</span>`;
}
async function installLicense(){
  const key = v('lic_key');
  if(!key) return toast('Paste a license key first', true);
  try {
    await api('/license', {method:'PUT', body: JSON.stringify({token:key})});
    document.getElementById('lic_key').value = '';
    toast('License installed - paid features unlocked.');
    // Re-fetch the session so new license features (identity/msp) apply without a page refresh.
    try { me = await api('/auth/me'); } catch {}
    try { renderNav(); renderTenantSelector(); } catch {}
    _license = null; loadLicense(); loadTenants();
  } catch(e){ toast('Install failed: ' + e.message, true); }
}
async function clearLicense(){
  if(!confirm('Remove the installed license? The instance reverts to the free Community tier (1 tenant, no Users & Access backup). Nothing is deleted - paid actions pause until a license is installed again.')) return;
  try {
    await api('/license', {method:'DELETE'});
    toast('License removed - running in Community tier.');
    _license = null; loadLicense(); loadTenants();
  } catch(e){ toast('Remove failed: ' + e.message, true); }
}

/* ---------- docs ---------- */
const DOC_TOPICS = [
  {key:'getting-started', title:'Getting started'},
  {key:'backups',         title:'Backups & snapshots'},
  {key:'explorer',        title:'Live State'},
  {key:'drift-events',    title:'Drift & events'},
  {key:'restore',         title:'Config restore'},
  {key:'identity',        title:'Users & Access backup'},
  {key:'alerts',          title:'Alerts & notifications'},
  {key:'users-security',  title:'Users & security'},
  {key:'licensing',       title:'Licensing & tiers'},
  {key:'msp-orgs',        title:'MSP & client orgs', feature:'msp'},
  {key:'deployment',      title:'Deployment & proxy'},
];
let _docKey = null;
function loadDocs(){
  // Topics may carry a `feature` key (e.g. 'msp'): those pages only appear when
  // the installed license includes that feature. Untagged topics show for everyone.
  const feats = (me && me.features) || (_license && _license.features) || [];
  const topics = DOC_TOPICS.filter(t=>!t.feature || feats.includes(t.feature));
  const tn = document.getElementById('doctopics');
  tn.innerHTML = topics.map(t=>`<button data-doc="${t.key}" onclick="openDoc('${t.key}')">${t.title}</button>`).join('');
  openDoc(_docKey || 'getting-started');
}
async function openDoc(key){
  _docKey = key;
  document.querySelectorAll('#doctopics button').forEach(b=>b.classList.toggle('active', b.dataset.doc===key));
  const body = document.getElementById('docbody');
  body.innerHTML = '<span class="muted">Loading…</span>';
  try {
    const r = await fetch(`/docs/${key}.html`);
    if(!r.ok) throw new Error('HTTP '+r.status);
    body.innerHTML = await r.text();
  } catch(e){ body.innerHTML = `<span class="muted">Failed to load doc: ${esc(String(e.message||e))}</span>`; }
}

async function loadSettings(){
  try {
    const s = await api('/settings');
    const smtp = s.smtp || {};
    document.getElementById('s_host').value = smtp.host || '';
    document.getElementById('s_port').value = smtp.port || 587;
    document.getElementById('s_tls').value = smtp.tls_mode || 'starttls';
    document.getElementById('s_user').value = smtp.username || '';
    document.getElementById('s_pass').value = '';
    document.getElementById('s_pass').placeholder = smtp.password_set ? 'leave blank to keep current' : 'password';
    document.getElementById('s_from').value = smtp.from_addr || '';
    document.getElementById('s_webhook').value = s.alert_webhook_url || '';
    document.getElementById('s_webhookfmt').value = s.alert_webhook_format || 'auto';
    const legacyEv = Array.isArray(s.alert_events) ? s.alert_events : ['drift_detected','backup_failed','restore_applied','identity_drift'];
    renderAlertEvents(Array.isArray(s.alert_events_email) ? s.alert_events_email : legacyEv,
                      Array.isArray(s.alert_events_webhook) ? s.alert_events_webhook : legacyEv);
    schedSet('sd', s.default_schedule_cron || null);
    schedSet('si', s.default_identity_schedule_cron || null);
    initTzPicker(s.org_timezone || 'UTC');
    document.getElementById('s_defkeep').value = s.default_retention_keep || '';
    document.getElementById('s_reserve').value = s.okta_rate_reserve_pct || '';
    document.getElementById('s_mfadays').value = (s.mfa_trust_days!=null? s.mfa_trust_days : '');
    document.getElementById('s_maxatt').value = (s.login_max_attempts!=null? s.login_max_attempts : '');
    document.getElementById('s_lockmin').value = (s.login_lockout_minutes!=null? s.login_lockout_minutes : '');
    document.getElementById('s_stale').value = (s.stale_backup_hours!=null? s.stale_backup_hours : '');
    document.getElementById('s_statemin').value = (s.state_poll_minutes!=null? s.state_poll_minutes : '');
    document.getElementById('s_userscache').value = (s.state_users_cache_minutes!=null? s.state_users_cache_minutes : '');
    document.getElementById('s_pub').value = s.public_url || '';
    document.getElementById('s_enforce').value = s.enforce_host ? 'true' : 'false';
  } catch(e){ toast(e.message, true); }
}
async function saveSettings(){
  const body = {
    smtp: { host: v('s_host'), port: parseInt(v('s_port')||'587'), tls_mode: v('s_tls'),
            username: v('s_user'), from_addr: v('s_from') },
    alert_webhook_url: v('s_webhook'),
    alert_webhook_format: v('s_webhookfmt'),
    alert_events_email: _alertCatsFromGroups(_alertGroupChecks('s_events_email')),
    alert_events_webhook: _alertCatsFromGroups(_alertGroupChecks('s_events_webhook')),
    default_schedule_cron: schedGet('sd') || '',
    default_identity_schedule_cron: schedGet('si') || '',
    org_timezone: v('s_tz') || 'UTC',
    default_retention_keep: parseInt(v('s_defkeep')||'0') || null,
    okta_rate_reserve_pct: parseInt(v('s_reserve')||'0') || null,
    mfa_trust_days: parseInt(v('s_mfadays')||'0'),
    login_max_attempts: parseInt(v('s_maxatt')||'0') || null,
    login_lockout_minutes: parseInt(v('s_lockmin')||'0') || null,
    stale_backup_hours: parseInt(v('s_stale')||'0') || null,
    state_poll_minutes: v('s_statemin')==='' ? null : parseInt(v('s_statemin')),
    state_users_cache_minutes: v('s_userscache')==='' ? null : parseInt(v('s_userscache')),
    public_url: v('s_pub') || null,
    enforce_host: v('s_enforce')==='true'
  };
  if(v('s_pass')) body.smtp.password = v('s_pass');
  try { await api('/settings', {method:'PUT', body: JSON.stringify(body)}); toast('Settings saved.'); loadSettings(); }
  catch(e){ toast('Save failed: '+e.message, true); }
}
async function testAlert(btn){
  btn.disabled = true; const old = btn.textContent; btn.textContent = 'Sending…';
  try{ const r = await api('/settings/test-alert', {method:'POST'});
    toast(`Test alert sent (format: ${r.format}, status ${r.status}). Check your channel.`);
  }catch(e){ toast('Test alert failed: '+e.message, true); }
  btn.disabled = false; btn.textContent = old;
}
async function testEmail(){
  if(!v('s_testto')) return toast('Enter a recipient address first', true);
  try { await api('/settings/test-email', {method:'POST', body: JSON.stringify({to: v('s_testto')})}); toast('Test email sent - check the inbox.'); }
  catch(e){ toast(e.message, true); }
}


/* ---------- events (tenant Activity page as of v1.2 Phase 1b) ---------- */
async function loadEvents(){
  const tid = currentTenantId;
  const type = document.getElementById('ev_type').value;
  const tb = document.getElementById('eventbody');
  if(!tid){ tb.innerHTML=emptyRow(5, EI.activity, 'No tenant selected.'); return; }
  tb.innerHTML=skelRows(5);
  try {
    const d = await api(`/tenants/${tid}/events?limit=200${type?'&event_type='+type:''}`);
    if(!d.events.length){ tb.innerHTML=emptyRow(5, EI.activity, 'No change events yet - these appear from the second backup onward.'); return; }
    tb.innerHTML = d.events.map(e=>`<tr>
      <td class="muted">${fmtLocal(e.at)}</td>
      <td><span class="evtype ev-${e.event_type}">${e.event_type}</span></td>
      <td>${esc(e.resource_type)}</td>
      <td>${esc(e.object_name||e.object_id||'-')}${e.detail&&e.detail.fields&&e.detail.fields.length?` <span class="muted">(${e.detail.fields.slice(0,6).join(', ')})</span>`:''}</td>
      <td class="muted">${fmtSnap(e.snapshot_ts)}</td></tr>`).join('');
  } catch(e){ tb.innerHTML=`<tr><td colspan="5" class="muted">${esc(e.message)}</td></tr>`; }
}

/* ---------- audit ---------- */
async function loadAudit(){
  const tb = document.getElementById('auditbody');
  tb.innerHTML = skelRows(4);
  try {
    const f = document.getElementById('au_filter').value;
    const d = await api(`/audit?limit=200${f?'&action='+f:''}`);
    if(!d.entries.length){ tb.innerHTML=emptyRow(4, EI.list, 'No audit entries yet.'); return; }
    tb.innerHTML = d.entries.map(a=>`<tr>
      <td class="muted">${fmtLocal(a.at)}</td>
      <td>${esc(a.actor)}</td><td>${esc(a.action)}</td>
      <td class="muted" style="font-size:.78rem">${esc(JSON.stringify(a.detail))}</td></tr>`).join('');
  } catch(e){ tb.innerHTML=`<tr><td colspan="4" class="muted">${esc(e.message)}</td></tr>`; }
}

