/* IdPVault - router.js: view switching + hash routing. Split from index.html (v1.2 Phase 1a). */
const VIEWS = ['dashboard','events','audit','users','orgs','docs','settings'];
function switchView(name, skipHash){
  if(!VIEWS.includes(name)) name = 'dashboard';
  // read-only users can't reach admin-only pages
  if(me && me.role !== 'admin' && ['audit','users','orgs','settings'].includes(name)) name = 'dashboard';
  VIEWS.forEach(x => {
    document.getElementById('view-'+x).classList.toggle('hidden', x !== name);
    const btn = document.querySelector(`nav button[data-view=${x}]`);
    if(btn) btn.classList.toggle('active', x === name);
  });
  if(name === 'dashboard') loadDashboard();
  if(name === 'events') loadEventTenants();
  if(name === 'audit') loadAudit();
  if(name === 'users') loadUsers();
  if(name === 'settings') loadSettings();
  if(name === 'docs') loadDocs();
  if(name === 'orgs') loadOrgs();
  const T={dashboard:'Dashboard',events:'Events',audit:'Audit log',users:'Users',orgs:'Client orgs',docs:'Docs',settings:'Settings'};
  const pt=document.getElementById('pagetitle'); if(pt) pt.textContent=T[name]||name;
  if(!skipHash && location.hash !== '#/'+name) location.hash = '#/'+name;
}
function currentRoute(){
  const m = location.hash.match(/^#\/([a-z]+)/);
  return m ? m[1] : 'dashboard';
}

