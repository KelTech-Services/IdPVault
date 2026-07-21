/* IdPVault - core.js: API client, shared globals/state, auth & profile, theme, toast, formatting and schedule-picker helpers.
   Split from index.html (v1.2 Phase 1a). Load order: core -> router -> pages/{fleet,tenant,admin} -> inline bootstrap in index.html. */
const api = (p, opts={}) => fetch('/api/v1'+p, {headers:{'Content-Type':'application/json'},...opts})
  .then(async r => {
    if(r.status === 401 && !p.startsWith('/auth/')){ showLogin(); throw new Error('session expired'); }
    if(!r.ok){ const b = await r.json().catch(()=>({})); throw new Error(b.detail || r.status); }
    return r.json();
  });

let me = null, _tenants = [], editingId = null, selectedSnaps = [], snapTenantId = null, _license = null;
let _orgs = [], _editOrgId = null;

/* ---------- theme ---------- */
function applyTheme(t){
  t = t === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem('idpv_theme', t); } catch {}
  // dark theme uses the light-on-dark logo; light theme the dark-on-light one
  document.querySelectorAll('img[src*="IdPVault_"]').forEach(img=>{
    img.src = t === 'light' ? '/IdPVault_logo.png' : '/IdPVault_light_logo.png';
  });
  const moon = document.getElementById('themeicon-moon'), sun = document.getElementById('themeicon-sun'),
        btn = document.getElementById('themebtn');
  if(moon){ moon.classList.toggle('hidden', t==='light'); sun.classList.toggle('hidden', t!=='light');
            btn.title = t === 'light' ? 'Switch to dark mode' : 'Switch to light mode'; }
  if(typeof renderCharts === 'function' && window._trendsReady !== false) try{ renderCharts(); }catch{}
  if(typeof renderTenantCharts === 'function') try{ renderTenantCharts(); }catch{}
}
async function toggleTheme(){
  const next = (document.documentElement.dataset.theme === 'light') ? 'dark' : 'light';
  applyTheme(next);
  if(me){ try{ await api('/auth/profile', {method:'POST', body: JSON.stringify({theme:next})}); me.theme = next; }catch{} }
}
try { applyTheme(localStorage.getItem('idpv_theme') || 'dark'); } catch { applyTheme('dark'); }


const EI = {
  db:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg>',
  activity:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
  list:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
  search:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
  users:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/></svg>',
};
function emptyRow(cols, icon, text, cta){
  return `<tr><td colspan="${cols}"><div class="empty">${icon||''}<div>${text}</div>${cta?`<div class="cta">${cta}</div>`:''}</div></td></tr>`;
}
function skelRows(cols, n=3){
  let out='';
  for(let i=0;i<n;i++){ let c=''; for(let j=0;j<cols;j++) c+=`<td><div class="skeleton" style="width:${45+((i*7+j*13)%40)}%"></div></td>`; out+=`<tr>${c}</tr>`; }
  return out;
}

const TIPI = '<span class="tipi">ⓘ</span>';   // visible tooltip marker; inherits the parent's title
function esc(s){ return String(s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function v(id){ return document.getElementById(id).value.trim(); }
function toast(msg, err=false){
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = err ? 'err' : '';
  t.classList.remove('hidden');
  clearTimeout(t._h); t._h = setTimeout(()=>t.classList.add('hidden'), 7000);
}


/* ---------- first-run setup ---------- */
async function doSetup(){
  document.getElementById('su_err').textContent='';
  if(v('su_pass') !== v('su_pass2')) return document.getElementById('su_err').textContent='Passwords do not match';
  try{
    me = await api('/auth/setup', {method:'POST', body: JSON.stringify({username:v('su_user'), password:v('su_pass')})});
    showApp();
  }catch(e){ document.getElementById('su_err').textContent = e.message; }
}
function showSetup(){
  document.getElementById('appview').classList.add('hidden');
  document.getElementById('loginview').classList.remove('hidden');
  document.getElementById('logincard').classList.add('hidden');
  document.getElementById('invitecard').classList.add('hidden');
  document.getElementById('setupcard').classList.remove('hidden');
}

/* ---------- profile: password + MFA ---------- */

async function saveProfileEmail(){
  try{ const r = await api('/auth/profile', {method:'POST', body: JSON.stringify({email:v('pf_email')})});
    me.email = r.email; toast('Email saved.');
  }catch(e){ toast(e.message, true); }
}
async function saveTimeFormat(){
  try{ const r = await api('/auth/profile', {method:'POST', body: JSON.stringify({time_format:v('pf_tf')})});
    me.time_format = r.time_format; initSchedPickers(); toast('Time format saved.');
  }catch(e){ toast(e.message, true); }
}
function showForgot(){ document.getElementById('forgotwrap').classList.toggle('hidden'); const f=document.getElementById('fg_id'); if(!document.getElementById('forgotwrap').classList.contains('hidden')) f.focus(); }
async function doForgot(){
  if(!v('fg_id')) return toast('Enter your username or email', true);
  try{ await api('/auth/forgot', {method:'POST', body: JSON.stringify({identifier:v('fg_id')})});
    document.getElementById('forgotwrap').classList.add('hidden');
    document.getElementById('l_err').textContent = 'If that account exists, a reset link has been sent to its email.';
  }catch(e){ toast(e.message, true); }
}

async function openProfile(){
  document.getElementById('pf_user').textContent = me.username;
  document.getElementById('pf_email').value = me.email || '';
  document.getElementById('pf_tf').value = me.time_format || 'auto';
  document.getElementById('pf_cur').value=''; document.getElementById('pf_new').value='';
  document.getElementById('profilemodal').classList.remove('hidden');
  renderMfa();
}
async function changePassword(){
  if(!v('pf_cur')||!v('pf_new')) return toast('Fill both password fields', true);
  try{ await api('/auth/change-password', {method:'POST', body: JSON.stringify({current_password:v('pf_cur'), new_password:v('pf_new')})});
    toast('Password updated.'); document.getElementById('pf_cur').value=''; document.getElementById('pf_new').value='';
  }catch(e){ toast(e.message, true); }
}
async function renderMfa(){
  const box = document.getElementById('pf_mfa');
  const enabled = me.mfa_enabled;
  if(enabled){
    box.innerHTML = `<p class="muted" style="font-size:.85rem">MFA is <b style="color:var(--green)">enabled</b> on your account.</p>
      <div style="margin-top:8px;display:flex;gap:8px;align-items:center"><input id="mfa_dis" inputmode="numeric" placeholder="current 6-digit code" style="width:200px"><button class="danger" onclick="mfaDisable()">Disable MFA</button></div>`;
  } else {
    box.innerHTML = `<p class="muted" style="font-size:.85rem">Add an authenticator app (Google Authenticator, Authy, 1Password) for a second sign-in factor.</p>
      <button class="primary" style="margin-top:8px" onclick="mfaSetup()">Set up MFA</button><div id="mfa_enroll"></div>`;
  }
}
async function mfaSetup(){
  try{
    const d = await api('/auth/mfa/setup', {method:'POST'});
    document.getElementById('mfa_enroll').innerHTML = `
      <div style="margin-top:12px;display:flex;gap:16px;align-items:center">
        <div style="background:#fff;padding:8px;border-radius:8px;width:170px;height:170px">${d.qr_svg}</div>
        <div style="font-size:.82rem">
          <p>Scan with your authenticator app, or enter this key manually:</p>
          <code style="display:inline-block;margin:6px 0;background:var(--bg);padding:4px 8px;border-radius:6px;word-break:break-all">${esc(d.secret)}</code>
          <div style="margin-top:8px;display:flex;gap:8px"><input id="mfa_code" inputmode="numeric" placeholder="6-digit code" style="width:160px"><button class="primary" onclick="mfaEnable()">Verify &amp; enable</button></div>
        </div></div>`;
  }catch(e){ toast(e.message, true); }
}
async function mfaEnable(){
  try{ await api('/auth/mfa/enable', {method:'POST', body: JSON.stringify({code:v('mfa_code')})});
    me.mfa_enabled = true; toast('MFA enabled - you\'ll be asked for a code at next sign-in.'); renderMfa();
  }catch(e){ toast(e.message, true); }
}
async function mfaDisable(){
  try{ await api('/auth/mfa/disable', {method:'POST', body: JSON.stringify({code:v('mfa_dis')})});
    me.mfa_enabled = false; toast('MFA disabled.'); renderMfa();
  }catch(e){ toast(e.message, true); }
}

/* ---------- auth ---------- */
async function boot(){
  const inviteTok = (location.hash.match(/invite=([\w-]+)/)||[])[1];
  if(inviteTok){ showInvite(); return; }
  try { const st = await api('/auth/status'); if(st.needs_setup){ showSetup(); return; } } catch {}
  try { me = await api('/auth/me'); showApp(); }
  catch { showLogin(); }
}
function showLogin(){
  document.getElementById('appview').classList.add('hidden');
  document.getElementById('loginview').classList.remove('hidden');
  document.getElementById('invitecard').classList.add('hidden');
  document.getElementById('logincard').classList.remove('hidden');
}
function showInvite(){
  document.getElementById('appview').classList.add('hidden');
  document.getElementById('loginview').classList.remove('hidden');
  document.getElementById('logincard').classList.add('hidden');
  document.getElementById('invitecard').classList.remove('hidden');
}
async function doLogin(){
  document.getElementById('l_err').textContent = '';
  const body = {username: v('l_user'), password: v('l_pass')};
  const totp = v('l_totp'); if(totp) body.totp = totp;
  try {
    const r = await api('/auth/login', {method:'POST', body: JSON.stringify(body)});
    if(r.mfa_required){
      document.getElementById('l_mfawrap').classList.remove('hidden');
      document.getElementById('l_totp').focus();
      document.getElementById('l_err').textContent = 'Enter your authenticator code.';
      return;
    }
    me = r; showApp();
  } catch(e){ document.getElementById('l_err').textContent = 'Sign-in failed: '+e.message; }
}
async function acceptInvite(){
  const tok = (location.hash.match(/invite=([\w-]+)/)||[])[1];
  if(v('i_pass') !== v('i_pass2')) return document.getElementById('i_err').textContent = 'Passwords do not match';
  try {
    const r = await api('/auth/accept-invite', {method:'POST', body: JSON.stringify({token: tok, password: v('i_pass')})});
    location.hash = ''; showLogin();
    document.getElementById('l_user').value = r.username;
    toast('Password set - sign in to continue.');
  } catch(e){ document.getElementById('i_err').textContent = e.message; }
}
async function doLogout(){ try{ await api('/auth/logout', {method:'POST'}); }catch{} location.reload(); }

async function showApp(){
  document.getElementById('loginview').classList.add('hidden');
  document.getElementById('appview').classList.remove('hidden');
  document.getElementById('whoami').textContent = me.username;
  document.getElementById('whoamirole').textContent = me.role;
  const isAdmin = me.role === 'admin';
  const canWrite = isAdmin || me.role === 'org_admin';
  document.querySelectorAll('.adminonly').forEach(el => el.classList.toggle('hidden', !isAdmin));
  document.querySelectorAll('.mutating').forEach(el => el.classList.toggle('hidden', !canWrite));
  if(me.theme) applyTheme(me.theme);
  initSchedPickers();
  health(); setInterval(health, 30000);
  startJobsPoll();
  setInterval(()=>{ const fv = document.getElementById('view-fleet'); if(fv && !fv.classList.contains('hidden')) loadDashboard(); }, 60000);
  // the router needs the visible-tenant list before it can render the nav or pick a landing page
  try { _tenants = await api('/tenants'); } catch { _tenants = []; }
  initRouter();
}
/* ---------- health ---------- */
async function health(){
  try { const h = await fetch('/healthz').then(r=>r.json());
    document.getElementById('healthdot').classList.add('ok');
    if(h.version) document.getElementById('appver').textContent = 'v'+h.version;
  } catch { document.getElementById('healthdot').classList.remove('ok'); }
}

function fmtUS(iso){ if(!iso) return ''; const [y,m,d] = iso.split('-'); return m && d ? `${m}/${d}/${y}` : iso; }
function fmtTs(ts){ return ts.replace(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/, '$1-$2-$3 $4:$5:$6'); }
function hour12Pref(){ const tf=(me&&me.time_format)||'auto'; return tf==='12'?true : tf==='24'?false : undefined; }
function fmtLocal(iso){ const o=hour12Pref(); return new Date(iso).toLocaleString([], o===undefined?{}:{hour12:o}); }
function snapDate(ts){ const m = String(ts).match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/); return m ? new Date(Date.UTC(+m[1], +m[2]-1, +m[3], +m[4], +m[5], +m[6])) : null; }
function fmtSnap(ts){ const d = snapDate(ts); return d ? fmtLocal(d.toISOString()) : String(ts); }
function fmtSnapDay(ts){
  // "Today, 4:56 PM" / "Yesterday, 4:56 PM" / full local date beyond that
  const d = snapDate(ts); if(!d) return String(ts);
  const now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  const o = hour12Pref();
  const time = d.toLocaleTimeString([], o === undefined ? {} : {hour12: o});
  if(days === 0) return 'Today, ' + time;
  if(days === 1) return 'Yesterday, ' + time;
  return fmtLocal(d.toISOString());
}
function cronLabel(cron){
  if(!cron) return 'not scheduled';
  let m;
  if((m = cron.match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/))) return 'Daily - ' + timeLabel(+m[2], +m[1]);
  if((m = cron.match(/^(\d{1,2}) (\d{1,2}) \* \* (\d)$/))) return 'Weekly - ' + SCHED_DAYS[+m[3]] + ' ' + timeLabel(+m[2], +m[1]);
  if((m = cron.match(/^(\d{1,2}) (\d{1,2}) (\d{1,2}) \* \*$/))) return 'Monthly - day ' + m[3] + ', ' + timeLabel(+m[2], +m[1]);
  return cron;
}

/* ---------- schedule pickers (friendly cron) ---------- */
const SCHED_DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
function timeLabel(h,m){
  const tf=(me&&me.time_format)||'auto';
  const use12 = tf==='12' || (tf==='auto' && /AM|PM/i.test(new Date(2000,0,1,13).toLocaleTimeString()));
  const mm = String(m).padStart(2,'0');
  if(!use12) return `${String(h).padStart(2,'0')}:${mm}`;
  const ap = h<12?'AM':'PM'; let hh=h%12; if(hh===0) hh=12;
  return `${hh}:${mm} ${ap}`;
}
function initSchedPickers(){
  const timeOpts = Array.from({length:48},(_,i)=>{const h=Math.floor(i/2),m=(i%2)*30;return `<option value="${h}:${m}">${timeLabel(h,m)}</option>`;}).join('');
  ['fs','fi','sd','si'].forEach(p=>{
    const host = document.getElementById('sched_'+p); if(!host) return;
    const prev = host.dataset.ready ? schedGet(p) : undefined;
    host.innerHTML = `<div style="display:flex;gap:6px;flex-wrap:wrap">
      <select id="${p}_freq" onchange="schedToggle('${p}')" style="width:auto"><option value="">Disabled</option>
        <option value="daily">Daily</option><option value="weekly">Weekly</option>
        <option value="monthly">Monthly</option><option value="cron">Custom (cron)</option></select>
      <select id="${p}_dow" class="hidden" style="width:auto">${SCHED_DAYS.map((d,i)=>`<option value="${i}">${d}</option>`).join('')}</select>
      <select id="${p}_dom" class="hidden" style="width:auto">${Array.from({length:28},(_,i)=>`<option value="${i+1}">Day ${i+1}</option>`).join('')}</select>
      <select id="${p}_time" class="hidden" style="width:auto">${timeOpts}</select>
      <input id="${p}_cron" class="hidden" placeholder="0 3 * * *" style="width:110px"><span id="${p}_croninfo" class="tipi hidden" title="Standard 5-field cron, evaluated in the org timezone">ⓘ</span></div>`;
    host.dataset.ready = '1';
    if(prev !== undefined) schedSet(p, prev);
  });
}
function schedToggle(p){
  const f = v(p+'_freq');
  document.getElementById(p+'_dow').classList.toggle('hidden', f!=='weekly');
  document.getElementById(p+'_dom').classList.toggle('hidden', f!=='monthly');
  document.getElementById(p+'_time').classList.toggle('hidden', f===''||f==='cron');
  document.getElementById(p+'_cron').classList.toggle('hidden', f!=='cron');
  const ci = document.getElementById(p+'_croninfo'); if(ci) ci.classList.toggle('hidden', f!=='cron');
}
function schedSet(p, cron){
  const el = k=>document.getElementById(p+k);
  if(!el('_freq')) return;
  let f='', h=3, m=0, mt;
  if(cron){
    if((mt=cron.match(/^(\d{1,2}) (\d{1,2}) \* \* \*$/)))      { f='daily';   m=+mt[1]; h=+mt[2]; }
    else if((mt=cron.match(/^(\d{1,2}) (\d{1,2}) \* \* (\d)$/))){ f='weekly';  m=+mt[1]; h=+mt[2]; el('_dow').value=mt[3]; }
    else if((mt=cron.match(/^(\d{1,2}) (\d{1,2}) (\d{1,2}) \* \*$/))){ f='monthly'; m=+mt[1]; h=+mt[2]; el('_dom').value=mt[3]; }
    else f='cron';
    if(f!=='cron' && m!==0 && m!==30) f='cron';        // odd minutes: show as raw cron
    if(f==='cron') el('_cron').value = cron;
  }
  el('_freq').value = f;
  if(f && f!=='cron') el('_time').value = `${h}:${m}`;
  schedToggle(p);
}
function schedGet(p){
  const f = v(p+'_freq');
  if(!f) return null;
  if(f==='cron') return v(p+'_cron').trim() || null;
  const [h,m] = v(p+'_time').split(':');
  if(f==='daily')  return `${m} ${h} * * *`;
  if(f==='weekly') return `${m} ${h} * * ${v(p+'_dow')}`;
  return `${m} ${h} ${v(p+'_dom')} * *`;
}
function initTzPicker(current){
  const sel = document.getElementById('s_tz'); if(!sel) return;
  let zones = ['UTC'];
  try { zones = Intl.supportedValuesOf('timeZone'); } catch {}
  if(!zones.includes('UTC')) zones = ['UTC', ...zones];
  if(current && !zones.includes(current)) zones = [current, ...zones];
  sel.innerHTML = zones.map(z=>`<option value="${z}"${z===current?' selected':''}>${z}</option>`).join('');
}

/* ---------- Simple Icons (bundled in the image; the app never calls out) ---------- */
let _si = null, _siPromise = null;
function siNorm(s){
  return String(s || '').toLowerCase()
    .replace(/\+/g, 'plus').replace(/\./g, 'dot').replace(/&/g, 'and')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]/g, '');
}
function siLoad(){
  if(_siPromise) return _siPromise;
  _siPromise = fetch('/vendor/simple-icons/si-map.json')
    .then(r => r.ok ? r.json() : null)
    .then(d => { _si = d; return d; })
    .catch(() => null);
  return _siPromise;
}

function siFind(name){
  if(!_si || !_si.colors) return null;
  const n = siNorm(name);
  if(!n) return null;
  const slug = (n in _si.colors) ? n : (_si.alias && _si.alias[n]) || null;
  return slug ? {slug: slug, hex: _si.colors[slug] || '888888'} : null;
}
function _chipHue(s){ let h = 0; for(let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
function appIconHtml(name){
  const m = siFind(name);
  if(m){
    const r = parseInt(m.hex.slice(0, 2), 16), g = parseInt(m.hex.slice(2, 4), 16), b = parseInt(m.hex.slice(4, 6), 16);
    const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
    const cls = 'appicon' + (lum < 0.18 ? ' si-dk' : '') + (lum > 0.82 ? ' si-lt' : '');
    const u = '/vendor/simple-icons/' + m.slug + '.svg';
    return `<span class="${cls}" style="--si:#${m.hex};-webkit-mask-image:url(${u});mask-image:url(${u})"></span>`;
  }
  const nm = String(name || '').trim();
  if(!nm) return '';
  const init = (nm.split(/\s+/).map(w => w[0]).join('').slice(0, 2)).toUpperCase();
  return `<span class="appchip" style="background:hsl(${_chipHue(nm)},35%,32%)">${esc(init)}</span>`;
}
function provTag(p){
  const u = '/vendor/simple-icons/' + esc(p) + '.svg';
  return `<span class="tag ${esc(p)}"><span class="appicon pi" style="-webkit-mask-image:url(${u});mask-image:url(${u})"></span>${esc(p)}</span>`;
}

/* ---------- instant tooltips: replace native title delay with our own box ---------- */
(function(){
  let box = null;
  function ensure(){
    if(!box){ box = document.createElement('div'); box.id = 'tipbox'; box.className = 'hidden'; document.body.appendChild(box); }
    return box;
  }
  function show(el){
    const t = el.dataset.tip; if(!t) return;
    const b = ensure();
    b.textContent = t;
    b.classList.remove('hidden');
    b.style.left = '0px'; b.style.top = '0px';
    const r = el.getBoundingClientRect(), bw = b.offsetWidth, bh = b.offsetHeight;
    let x = r.left + r.width / 2 - bw / 2;
    x = Math.max(8, Math.min(x, window.innerWidth - bw - 8));
    let y = r.bottom + 8;
    if(y + bh > window.innerHeight - 8) y = r.top - bh - 8;
    b.style.left = x + 'px'; b.style.top = y + 'px';
  }
  function hide(){ if(box) box.classList.add('hidden'); }
  document.addEventListener('mouseover', function(e){
    const el = e.target.closest ? e.target.closest('[title], [data-tip]') : null;
    if(!el){ hide(); return; }
    if(el.hasAttribute('title')){
      const t = el.getAttribute('title');
      el.removeAttribute('title');
      if(t){ el.dataset.tip = t; if(!el.hasAttribute('aria-label')) el.setAttribute('aria-label', t); }
    }
    if(el.dataset.tip) show(el); else hide();
  });
  document.addEventListener('mouseout', function(e){
    const el = e.target.closest ? e.target.closest('[data-tip]') : null;
    if(el && !(e.relatedTarget && el.contains(e.relatedTarget))) hide();
  });
  document.addEventListener('scroll', hide, true);
  document.addEventListener('click', hide, true);
})();


/* ---------- background jobs (nav activity area + page progress) ---------- */
let _jobsTimer = null;
let _jobsPrev = {};   // job id -> last seen status, to detect completion transitions
window._jobDoneHooks = window._jobDoneHooks || [];
const JOB_KIND_LABEL = {config_backup: 'Config backup',
                        identity_backup: 'Users & Access backup',
                        identity_restore: 'Users & Access restore'};

function jobPct(j){
  return (j.progress_total && j.progress_total > 0)
    ? Math.min(100, Math.round(100 * j.progress_done / j.progress_total)) : null;
}

async function pollJobs(){
  let jobs;
  try { jobs = await api('/jobs/active'); } catch { return; }  // transient failures stay silent
  renderNavJobs(jobs);
  jobs.forEach(j => {
    const prev = _jobsPrev[j.id];
    if((prev === 'queued' || prev === 'running') && (j.status === 'ok' || j.status === 'failed')){
      (window._jobDoneHooks || []).forEach(fn => { try { fn(j); } catch {} });
    }
    _jobsPrev[j.id] = j.status;
  });
}

function startJobsPoll(){
  if(_jobsTimer) return;
  pollJobs();
  _jobsTimer = setInterval(pollJobs, 4000);
}

function renderNavJobs(jobs){
  const box = document.getElementById('navjobs');
  if(!box) return;
  if(!jobs.length){ box.classList.add('hidden'); box.innerHTML = ''; return; }
  box.classList.remove('hidden');
  box.innerHTML = '<div class="njtitle" title="Backups and restores currently queued or running. Click a row to open that tenant.">Activity <span class="tipi">ⓘ</span></div>' + jobs.map(j => {
    const pct = jobPct(j);
    const label = `<div class="njlabel">${esc(j.tenant_name)}</div><div class="njkind">${JOB_KIND_LABEL[j.kind] || esc(j.kind)}</div>`;
    let body = '';
    if(j.status === 'running'){
      body = pct != null
        ? `<div class="njbar"><div class="njfill" style="width:${pct}%"></div></div><div class="njmeta">${pct}%</div>`
        : `<div class="njbar njind"><div class="njfill"></div></div><div class="njmeta">${j.progress_done ? j.progress_done + ' API calls' : 'running'}</div>`;
    } else if(j.status === 'queued'){
      body = '<div class="njmeta">queued</div>';
    } else if(j.status === 'ok'){
      body = '<div class="njmeta st-created">done</div>';
    } else {
      body = `<div class="njmeta st-failed" title="${esc(j.error || 'unknown error')}">failed <span class="tipi">ⓘ</span></div>`;
    }
    return `<div class="njrow" onclick="jobJump(${j.tenant_id})">${label}${body}</div>`;
  }).join('');
}

function jobJump(tid){ try { onTenantSelect(String(tid)); } catch {} }

async function waitForJob(jobId, onTick){
  for(;;){
    const j = await api('/jobs/' + jobId);
    if(onTick) try { onTick(j); } catch {}
    if(j.status === 'ok') return j;
    if(j.status === 'failed') throw new Error(j.error || 'job failed');
    await new Promise(r => setTimeout(r, 2500));
  }
}
