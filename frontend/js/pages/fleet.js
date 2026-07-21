/* IdPVault - pages/fleet.js: all-tenants dashboard (cards, charts, unbacked changes, MSP renewals). Split from index.html (v1.2 Phase 1a). */
async function loadRenewals(){
  const el = document.getElementById('renewalpanel'); if(!el) return;
  if(!(me && me.role==='admin' && (me.features||[]).includes('msp'))){ el.classList.add('hidden'); return; }
  try {
    const r = await api('/orgs/renewals?days=60');
    if(!r.length){ el.classList.add('hidden'); return; }
    el.innerHTML = `<section class="panel"><h2>Client renewals - next 60 days</h2>
      <table><thead><tr><th>Org</th><th>Renewal date</th><th>Billing</th></tr></thead><tbody>` +
      r.map(o=>`<tr><td>${esc(o.name)}</td>
        <td>${o.overdue?`<span class="st-failed">${fmtUS(o.renewal_date)} (overdue)</span>`:fmtUS(o.renewal_date)}</td>
        <td class="muted">${esc(o.billing_memo||'-')}${o.billing_cadence?` · ${o.billing_cadence}`:''}</td></tr>`).join('') +
      `</tbody></table></section>`;
    el.classList.remove('hidden');
  } catch { el.classList.add('hidden'); }
}

/* ---------- dashboard cards ---------- */
function fmtBytes(n){ if(!n) return '0 B'; const u=['B','KB','MB','GB']; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++;} return n.toFixed(i?1:0)+' '+u[i]; }
let _dashData = null;
async function loadDashboard(){
  const el = document.getElementById('statcards'); if(!el) return;
  try {
    const d = await api('/dashboard/summary');
    _dashData = d;
    const cov = d.coverage, pct = cov.total ? Math.round(cov.ok/cov.total*100) : 0;
    const covClass = cov.total===0?'':pct===100?'good':pct>=50?'warn':'bad';
    const unbacked = d.tenants.reduce((a,t)=> a + (t.unbacked_changes||0), 0);
    el.innerHTML = `
      <div class="card ${covClass}"><div class="lbl2">Data coverage</div><div class="big">${cov.total? (pct===100?'Excellent':pct+'%') : '-'}</div><div class="sub">${cov.ok}/${cov.total} tenant(s) backed up on schedule</div></div>
      <div class="card"><div class="lbl2">Tenants</div><div class="big">${d.tenants.length}</div><div class="sub">${d.tenants.map(t=>t.provider).filter((v,i,a)=>a.indexOf(v)===i).join(', ')||'none'}</div></div>
      <div class="card ${unbacked>0?'warn':''}" style="cursor:pointer" onclick="toggleUnbacked()"><div class="lbl2">Unbacked config changes</div><div class="big">${unbacked===0?'0':unbacked}</div><div class="sub">since last backup · click for breakdown</div></div>
      <div class="card"><div class="lbl2">Storage used <span class="tipi" title="All backups across all tenants - config snapshots, Full-DR database dumps, and Users & Access snapshots">ⓘ</span></div><div class="big">${fmtBytes(d.storage_bytes)}</div><div class="sub">${d.events_7d} change events in 7 days</div></div>`;
    if(!document.getElementById('unbackedpanel').classList.contains('hidden')) renderUnbacked();
    loadCharts();
    loadRenewals();
  } catch(e){ el.innerHTML = `<div class="card bad"><div class="lbl2">Dashboard</div><div class="sub">${esc(e.message)}</div></div>`; }
}
let _trends = null, _charts = [];
function _cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
async function loadCharts(){
  const row = document.getElementById('chartrow');
  const VC = window.VChart && (window.VChart.VChart || window.VChart.default);
  if(!row || !VC) return;
  try { _trends = await api('/dashboard/trends'); } catch { return; }
  if(!(_trends.storage_by_tenant||[]).length){ row.classList.add('hidden'); return; }
  row.classList.remove('hidden');
  renderCharts();
}
function renderCharts(){
  const VC = window.VChart && (window.VChart.VChart || window.VChart.default);
  if(!VC || !_trends) return;
  _charts.forEach(c=>{ try{ c.release(); }catch{} }); _charts = [];
  const light = document.documentElement.dataset.theme === 'light';
  const G = _cssVar('--green'), A = _cssVar('--amber'), R = _cssVar('--red'),
        B = _cssVar('--accent'), V = _cssVar('--violet'), GD = _cssVar('--gold');
  const base = { background: 'transparent' };
  const opts = el => ({ dom: el, theme: light ? 'light' : 'dark' });
  const md = s => s.slice(5);   // YYYY-MM-DD -> MM-DD
  const evRows = [];
  (_trends.events_daily||[]).forEach(d=>['add','update','delete'].forEach(k=>evRows.push({date: md(d.date), type: k, n: d[k]||0})));
  const runRows = [];
  (_trends.runs_daily||[]).forEach(d=>['ok','failed'].forEach(k=>runRows.push({date: md(d.date), type: k, n: d[k]||0})));
  const stRows = (_trends.storage_by_tenant||[]).map(s=>({name: s.name, mb: Math.max(Math.round(s.bytes/1048576*10)/10, 0.1)}));
  const specs = [
    ['ch_events', {...base, type:'bar', data:[{id:'d', values: evRows}], xField:'date', yField:'n',
      seriesField:'type', stack:true, color:[G, A, R],
      legends:{visible:true, orient:'bottom', item:{label:{style:{fontSize:11}}}},
      axes:[{orient:'left', label:{style:{fontSize:10}}, grid:{visible:false}},
            {orient:'bottom', label:{style:{fontSize:9}, sampling:true}}]}],
    ['ch_runs', {...base, type:'bar', data:[{id:'d', values: runRows}], xField:'date', yField:'n',
      seriesField:'type', stack:true, color:[B, R],
      legends:{visible:true, orient:'bottom', item:{label:{style:{fontSize:11}}}},
      axes:[{orient:'left', label:{style:{fontSize:10}}, grid:{visible:false}},
            {orient:'bottom', label:{style:{fontSize:9}, sampling:true}}]}],
    ['ch_storage', {...base, type:'pie', data:[{id:'d', values: stRows}], valueField:'mb',
      categoryField:'name', innerRadius:0.62, outerRadius:0.85, color:[B, GD, V, G, A, R],
      label:{visible:false},
      legends:{visible:true, orient:'bottom', item:{label:{style:{fontSize:11}}}},
      tooltip:{mark:{content:[{key: d=>d.name, value: d=>d.mb+' MB'}]}}}],
  ];
  specs.forEach(([id, spec])=>{
    const el = document.getElementById(id); if(!el) return;
    el.innerHTML = '';
    try { const ch = new VC(spec, opts(el)); ch.renderSync ? ch.renderSync() : ch.render(); _charts.push(ch); }
    catch(e){ console.warn('chart', id, e); }
  });
}
function toggleUnbacked(){
  const el = document.getElementById('unbackedpanel');
  if(!el.classList.contains('hidden')){ el.classList.add('hidden'); return; }
  renderUnbacked(); el.classList.remove('hidden');
}
function renderUnbacked(){
  const el = document.getElementById('unbackedpanel');
  const d = _dashData; if(!d) return;
  const rows = d.tenants.map(t=>{
    const tt = _tenants.find(x=>x.id===t.id);
    const cnt = t.unbacked_changes;
    const cntCell = cnt==null ? '<span class="muted">n/a <span class="tipi" title="No successful backup yet, or the provider does not report admin events">ⓘ</span></span>' : (cnt>0?`<span class="ev-update"><b>${cnt}</b></span>`:'0');
    const lastCell = t.last_run ? `${fmtSnap(t.last_run.ts)}${t.last_run.status!=='ok'?` <span class="st-failed">(${t.last_run.status})</span>`:''}` : '<span class="muted">never</span>';
    const btn = (me && (me.role==='admin' || me.role==='org_admin')) ? `<button ${tt&&tt.active===false?`disabled title="${LIC_TIP_TENANT}"`:''} onclick="backupNow(${t.id}, this)">Backup config now${tt&&tt.active===false?' '+TIPI:''}</button>` : '';
    return `<tr><td>${esc(t.name)}</td><td>${provTag(t.provider)}</td><td>${cntCell}</td><td class="muted">${lastCell}</td><td>${btn}</td></tr>`;
  }).join('');
  el.innerHTML = `<section class="panel"><h2>Unbacked config changes by tenant <span class="spacer"></span><button onclick="document.getElementById('unbackedpanel').classList.add('hidden')">Close</button></h2>
    <p class="muted" style="font-size:.8rem;margin-bottom:8px">Changes counted from the provider's own event log since the last successful backup. The exact change list comes from the next backup's diff - run a backup to capture them.</p>
    <table><thead><tr><th>Tenant</th><th>Provider</th><th>Unbacked config changes</th><th>Last backup</th><th></th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

