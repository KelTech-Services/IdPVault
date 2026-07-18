/* IdPVault - router.js: nav shell, tenant selector, hash routing, visibility matrix (v1.2 Phase 1b).
   Routes: #/t/<tenant_id>/<page> for tenant workspace pages, #/<page> for global pages.
   Visibility follows UI-SPEC sec 2: role + license flags decide what renders; server enforces. */

let currentTenantId = null;   // numeric tenant id inside a tenant workspace, null on global pages
let _adminOpen = true;
let _activeHash = '';

const NAV_ICONS = {
  overview: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>',
  backups: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
  identity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  activity: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  tsettings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="21" y1="4" x2="14" y2="4"/><line x1="10" y1="4" x2="3" y2="4"/><line x1="21" y1="12" x2="12" y2="12"/><line x1="8" y1="12" x2="3" y2="12"/><line x1="21" y1="20" x2="16" y2="20"/><line x1="12" y1="20" x2="3" y2="20"/><line x1="14" y1="2" x2="14" y2="6"/><line x1="8" y1="10" x2="8" y2="14"/><line x1="16" y1="18" x2="16" y2="22"/></svg>',
  fleet: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
  orgs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>',
  appusers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  audit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/><rect x="9" y="3" width="6" height="4" rx="1"/><line x1="9" y1="12" x2="15" y2="12"/><line x1="9" y1="16" x2="13" y2="16"/></svg>',
  license: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="21" y1="4" x2="14" y2="4"/><line x1="10" y1="4" x2="3" y2="4"/><line x1="21" y1="12" x2="12" y2="12"/><line x1="8" y1="12" x2="3" y2="12"/><line x1="21" y1="20" x2="16" y2="20"/><line x1="12" y1="20" x2="3" y2="20"/><line x1="14" y1="2" x2="14" y2="6"/><line x1="8" y1="10" x2="8" y2="14"/><line x1="16" y1="18" x2="16" y2="22"/></svg>',
  docs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
};

const GLOBAL_VIEWS = {fleet:'view-fleet', orgs:'view-orgs', appusers:'view-users', audit:'view-audit', license:'view-license', settings:'view-settings', docs:'view-docs'};
const TENANT_VIEWS = {overview:'view-t-overview', backups:'view-t-backups', identity:'view-t-identity', activity:'view-t-activity', settings:'view-t-settings'};
const GLOBAL_TITLES = {fleet:'Dashboard', orgs:'Client orgs', appusers:'App users', audit:'Audit log', license:'License', settings:'System settings', docs:'Docs'};
const TENANT_TITLES = {overview:'Overview', backups:'Backups', identity:'Users & Access', activity:'Activity', settings:'Settings'};
const LEGACY_ROUTES = {dashboard:'fleet', users:'appusers', events:'fleet'};

function _isAdmin(){ return me && me.role === 'admin'; }
function _canWrite(){ return me && (me.role === 'admin' || me.role === 'org_admin'); }
function _feat(f){ return ((me && me.features) || []).includes(f); }
function visibleTenants(){ return _tenants || []; }
function currentTenant(){ return visibleTenants().find(x => x.id === currentTenantId) || null; }

function tenantPages(t){
  const pages = [
    {key:'overview', label:'Overview'},
    {key:'backups', label:'Backups'},
  ];
  // HIDE not lock-badge (spec sec 2): identity page only when licensed, provider-supported,
  // tenant not license-paused, and the user can write (org_viewer has no identity actions today).
  if(_feat('identity') && t && t.supports_identity !== false && t.active !== false && _canWrite())
    pages.push({key:'identity', label:'Users & Access'});
  pages.push({key:'activity', label:'Activity'});
  if(_canWrite()) pages.push({key:'settings', label:'Settings'});
  return pages;
}
function adminPages(){
  if(!_isAdmin()) return [];
  const p = [];
  if(_feat('msp')) p.push({key:'orgs', label:'Orgs'});
  if(_feat('identity')) p.push({key:'appusers', label:'App users'});
  p.push({key:'audit', label:'Audit'});
  p.push({key:'license', label:'License'});
  p.push({key:'settings', label:'System settings'});
  return p;
}
function defaultRoute(){
  const ts = visibleTenants();
  return ts.length === 1 ? `#/t/${ts[0].id}/overview` : '#/fleet';
}

/* ---------- sidebar rendering ---------- */
function renderTenantSelector(){
  const wrap = document.getElementById('tenantselwrap'), sel = document.getElementById('tenantsel');
  if(!wrap || !sel) return;
  const ts = visibleTenants();
  if(ts.length <= 1){ wrap.classList.add('hidden'); return; }
  wrap.classList.remove('hidden');
  sel.innerHTML = '<option value="all">All tenants</option>' +
    ts.map(t => `<option value="${t.id}">${esc(t.name)}${t.org_name ? ' · ' + esc(t.org_name) : ''}</option>`).join('');
  sel.value = currentTenantId ? String(currentTenantId) : 'all';
}
function onTenantSelect(v){
  location.hash = v === 'all' ? '#/fleet' : '#/t/' + v + '/overview';
}
function _navBtn(hash, label, icon){
  return `<button data-route="${hash}" class="${hash === _activeHash ? 'active' : ''}" onclick="location.hash='${hash}'">${icon || ''}<span>${esc(label)}</span></button>`;
}
function renderNav(){
  const nav = document.getElementById('mainnav');
  if(!nav) return;
  const t = currentTenant();
  let html = '';
  if(t){
    if(visibleTenants().length > 1) html += `<div class="navgroup">${esc(t.name)}</div>`;
    html += tenantPages(t).map(p => _navBtn(`#/t/${t.id}/${p.key}`, p.label, NAV_ICONS[p.key === 'settings' ? 'tsettings' : p.key])).join('');
  } else {
    html += _navBtn('#/fleet', 'Dashboard', NAV_ICONS.fleet);
  }
  const ap = adminPages();
  if(ap.length){
    html += `<div class="navgroup navgroup-admin" onclick="toggleAdminGroup()">Administration <span>${_adminOpen ? '▾' : '▸'}</span></div>`;
    if(_adminOpen) html += ap.map(p => _navBtn('#/' + p.key, p.label, NAV_ICONS[p.key])).join('');
  }
  nav.innerHTML = html;
  const bn = document.getElementById('sb_backupnow');
  if(bn) bn.classList.toggle('hidden', !(t && _canWrite() && t.active !== false));
}
function toggleAdminGroup(){ _adminOpen = !_adminOpen; renderNav(); }
function sbBackupNow(btn){ if(currentTenantId) backupNow(currentTenantId, btn); }

/* ---------- routing ---------- */
function _showView(viewId, title){
  Object.values(GLOBAL_VIEWS).concat(Object.values(TENANT_VIEWS)).forEach(id => {
    const el = document.getElementById(id);
    if(el) el.classList.toggle('hidden', id !== viewId);
  });
  const pt = document.getElementById('pagetitle');
  if(pt) pt.textContent = title;
}
function route(){
  let m, h = location.hash;
  if((m = h.match(/^#\/t\/(\d+)\/([a-z]+)/))){
    const id = +m[1], t = visibleTenants().find(x => x.id === id);
    if(!t){ location.hash = defaultRoute(); return; }
    const pages = tenantPages(t);
    let page = pages.some(p => p.key === m[2]) ? m[2] : 'overview';
    currentTenantId = id;
    _activeHash = `#/t/${id}/${page}`;
    if(location.hash !== _activeHash){ location.hash = _activeHash; return; }
    _showView(TENANT_VIEWS[page], `${t.name} - ${TENANT_TITLES[page]}`);
    renderTenantSelector(); renderNav();
    enterTenantPage(t, page);
    return;
  }
  m = h.match(/^#\/([a-z]+)/);
  let page = m ? (LEGACY_ROUTES[m[1]] || m[1]) : '';
  if(!GLOBAL_VIEWS[page]){ location.hash = defaultRoute(); return; }
  const adminOnly = ['orgs', 'appusers', 'audit', 'license', 'settings'];
  if(adminOnly.includes(page) && !adminPages().some(p => p.key === page)){ location.hash = defaultRoute(); return; }
  if(page === 'fleet' && visibleTenants().length === 1){ location.hash = defaultRoute(); return; }
  if(page === 'fleet') currentTenantId = null;
  _activeHash = '#/' + page;
  if(location.hash !== _activeHash){ location.hash = _activeHash; return; }
  _showView(GLOBAL_VIEWS[page], GLOBAL_TITLES[page]);
  renderTenantSelector(); renderNav();
  enterGlobalPage(page);
}
function enterGlobalPage(page){
  if(page === 'fleet'){ loadDashboard(); loadTenants(); }
  if(page === 'orgs') loadOrgs();
  if(page === 'appusers') loadUsers();
  if(page === 'audit') loadAudit();
  if(page === 'license') loadLicense();
  if(page === 'settings') loadSettings();
  if(page === 'docs') loadDocs();
}
function enterTenantPage(t, page){
  if(page === 'overview') renderTenantOverview(t);
  if(page === 'backups') showSnaps(t.id, t.slug);
  if(page === 'identity') openIdentity(t.id, t.slug);
  if(page === 'activity') loadEvents();
  if(page === 'settings') mountTenantSettings(t);
}
function initRouter(){
  window.addEventListener('hashchange', route);
  if(!location.hash || !location.hash.startsWith('#/')) location.hash = defaultRoute();
  route();
}
