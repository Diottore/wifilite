#!/usr/bin/env python3
"""
run_tests.py

Termux Network Tester — Mobile UI with SSE live updates and robust ping

Save as run_tests.py and run:
  python run_tests.py --host 0.0.0.0 --port 5000

Requirements:
  pkg install iperf3 termux-api
  pip install Flask
"""
import os
import subprocess
import threading
import time
import json
import csv
from datetime import datetime
from statistics import mean, median
from flask import Flask, request, jsonify, send_file, render_template_string, Response, stream_with_context

APP = Flask(__name__)

# ---------- Global state ----------
state = {
    "running": False,
    "paused": False,
    "stop_requested": False,
    "current_location_idx": 0,
    "current_iteration": 0,
    "logs": [],
    "summary": {},
    "last_message": "",
    "config": {},
    "live": { "location": None, "iteration": None, "baseline": [], "upload": [], "download": [] }
}

state_lock = threading.Lock()

LOCATIONS = ["p1","p2","p3","p4","p5","p6","p7","p8"]

# ---------- HTML UI template (mobile, SSE client) ----------
INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Termux Network Tester — Live (SSE)</title>
<style>
  :root{--bg:#0f172a;--muted:#94a3b8;--accent:#60a5fa;--accent-2:#7dd3fc;--glass:rgba(255,255,255,0.03);--card-radius:12px;--gap:12px;--btn-h:48px;font-family:Inter,Roboto,Arial,sans-serif;color-scheme:dark}
  html,body{margin:0;padding:0;height:100%;background:linear-gradient(180deg,var(--bg),#071029);color:#e6eef8}
  .app{max-width:900px;margin:0 auto;padding:12px;box-sizing:border-box}
  header{display:flex;align-items:center;gap:10px;padding-bottom:6px}
  .logo{width:44px;height:44px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:flex;align-items:center;justify-content:center;font-weight:700;color:#021028}
  h1{font-size:1.05rem;margin:0}.lead{font-size:0.86rem;color:var(--muted);margin-top:2px}
  main{display:flex;flex-direction:column;gap:var(--gap);padding-bottom:84px}
  .card{background:linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.02));border-radius:var(--card-radius);padding:12px}
  input, select{background:transparent;border:1px solid rgba(255,255,255,0.06);padding:10px 12px;border-radius:10px;color:inherit;font-size:0.95rem;min-width:110px}
  .controls{display:flex;flex-direction:column;gap:8px}
  .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
  button.btn{height:var(--btn-h);border-radius:12px;border:0;padding:0 14px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;gap:8px}
  .btn.primary{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028}
  .btn.ghost{background:transparent;border:1px solid rgba(255,255,255,0.04);color:#dbeafe}
  .btn.warn{background:linear-gradient(90deg,#f97316,#fb7185);color:#071023}
  .pill{background:var(--glass);padding:8px 10px;border-radius:999px;font-weight:700;color:#dbeafe}
  .summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:8px}
  .s-card{padding:10px;border-radius:10px;background:linear-gradient(180deg, rgba(255,255,255,0.018), rgba(255,255,255,0.01));border:1px solid rgba(255,255,255,0.02)}
  .chart{ margin-top:8px; border-radius:10px; overflow:hidden; padding:8px }
  canvas{width:100% !important;height:120px !important;display:block}
  .logbox{font-family:monospace;background:rgba(255,255,255,0.02);padding:8px;border-radius:8px;max-height:220px;overflow:auto;color:#cfe7ff}
  .bottom-bar{position:fixed;left:0;right:0;bottom:0;background:linear-gradient(180deg, rgba(2,6,23,0.9), rgba(2,6,23,0.95));padding:10px 12px;display:flex;align-items:center;gap:8px;justify-content:space-between}
  .loc-list{display:flex;gap:6px;overflow:auto;padding:6px;background:transparent}
  .loc-btn{min-width:44px;height:44px;border-radius:10px;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.02);display:flex;align-items:center;justify-content:center;font-weight:700;color:#dbeafe}
  .loc-btn.active{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028}
  @media (max-width:420px){canvas{height:92px !important}.controls .row{flex-direction:column}.loc-btn{min-width:40px;height:40px;font-size:0.86rem}}
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">TN</div>
    <div>
      <h1>Termux Network Tester</h1>
      <div class="lead">Baseline + ping por fase · p1..p8 · live</div>
    </div>
  </header>

  <main>
    <section class="card" id="controlsCard">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Controles</strong><div class="pill" id="statusPill">idle</div>
      </div>
      <div class="controls" style="margin-top:10px">
        <div class="row">
          <input id="serverInput" type="text" value="192.168.1.100">
          <select id="itersSelect"><option>3</option><option>4</option><option selected>5</option></select>
        </div>
        <div class="row" style="margin-top:8px">
          <input id="durationInput" type="number" min="10" value="60" style="max-width:140px">
          <input id="baselineInput" type="number" min="2" max="30" value="8" style="max-width:120px">
          <input id="pingInterval" type="number" step="0.05" value="0.2" style="max-width:120px">
          <label style="display:flex;align-items:center;gap:6px;color:var(--muted)"><input id="autoChk" type="checkbox"> Auto</label>
        </div>
        <div class="actions">
          <button class="btn primary" id="startBtn">▶ Iniciar</button>
          <button class="btn ghost" id="pauseBtn">⏸ Pausar</button>
          <button class="btn ghost" id="resumeBtn">⏯ Reanudar</button>
          <button class="btn warn" id="stopBtn">■ Detener</button>
          <button class="btn ghost" id="jsonBtn">⬇ JSON</button>
          <button class="btn ghost" id="csvBtn">⬇ CSV</button>
          <button class="btn ghost" id="clearLocBtn">🧹 Limpiar ubicación</button>
          <button class="btn ghost" id="clearAllBtn">🧼 Limpiar todo</button>
        </div>
        <div class="status" style="margin-top:8px"><div style="flex:1"><div class="muted" id="lastMsg">(esperando...)</div></div></div>
      </div>
    </section>

    <section class="card" id="summaryCard">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Resumen de ubicación</strong><div class="muted" id="summaryLoc">—</div>
      </div>
      <div class="summary-grid">
        <div class="s-card"><h4>Ping baseline</h4><div class="s-val" id="sb_baseline">—</div></div>
        <div class="s-card"><h4>Ping upload</h4><div class="s-val" id="sb_up">—</div></div>
        <div class="s-card"><h4>Ping download</h4><div class="s-val" id="sb_down">—</div></div>
        <div class="s-card"><h4>Throughput</h4><div class="s-val" id="sb_thr">—</div></div>
      </div>
      <div class="chart" id="charts">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <div style="flex:1;min-width:180px"><div class="chart-title muted">Baseline RTT (ms)</div><canvas id="chartBaseline"></canvas></div>
          <div style="flex:1;min-width:180px"><div class="chart-title muted">RTT upload / download (ms)</div><canvas id="chartRTT"></canvas></div>
        </div>
        <div style="margin-top:8px"><div class="chart-title muted">Throughput (Mbps)</div><canvas id="chartThroughput"></canvas></div>
      </div>
      <div style="margin-top:10px" id="summaryTable"></div>
    </section>

    <section class="card" id="logsCard"><strong>Logs recientes</strong><div class="logbox" id="logbox">(no hay logs todavía)</div></section>
  </main>
</div>

<div class="bottom-bar"><div class="loc-list" id="locList"></div><div class="bottom-group"><button class="btn small ghost" id="prevLoc">◀</button><button class="btn small ghost" id="nextLoc">▶</button></div></div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const LOCS = @@LOCATIONS@@;
let charts = { baseline:null, rtt:null, thr:null };
const $ = id => document.getElementById(id);

function initLocButtons(){
  const container = $('locList'); container.innerHTML = '';
  LOCS.forEach((l)=>{ const b=document.createElement('button'); b.className='loc-btn'; b.innerText=l.toUpperCase(); b.dataset.loc=l; b.addEventListener('click',()=>selectLocation(l)); container.appendChild(b); });
}
function setActiveLocButton(loc){ document.querySelectorAll('.loc-btn').forEach(b=>b.classList.toggle('active', b.dataset.loc===loc)); }

function createCharts(){
  if(charts.baseline) return;
  charts.baseline = new Chart($('chartBaseline').getContext('2d'), { type:'line', data:{ labels:[], datasets:[{label:'baseline', data:[], borderColor:'#60a5fa', tension:0.3, pointRadius:0}]}, options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{display:false}}}});
  charts.rtt = new Chart($('chartRTT').getContext('2d'), { type:'line', data:{ labels:[], datasets:[{label:'upload', data:[], borderColor:'#34d399'},{label:'download', data:[], borderColor:'#fb7185'}]}, options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}}}});
  charts.thr = new Chart($('chartThroughput').getContext('2d'), { type:'line', data:{ labels:[], datasets:[{label:'upload Mbps', data:[], borderColor:'#a78bfa'},{label:'download Mbps', data:[], borderColor:'#fb923c'}]}, options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}}}});
}

function mergeHistoryWithLive(history, live){
  const iters = (history && history.iterations) ? history.iterations.slice() : [];
  const base = (history && history.baseline_mean) ? history.baseline_mean.slice() : [];
  const up = (history && history.up_mean) ? history.up_mean.slice() : [];
  const down = (history && history.down_mean) ? history.down_mean.slice() : [];
  const upThr = (history && history.upload_mbps) ? history.upload_mbps.slice() : [];
  const downThr = (history && history.download_mbps) ? history.download_mbps.slice() : [];
  if(live && live.location){
    const liveIter = live.iteration;
    const liveBaseMean = (live.baseline && live.baseline.length) ? (live.baseline.reduce((a,b)=>a+b,0)/live.baseline.length) : null;
    const liveUpMean = (live.upload && live.upload.length) ? (live.upload.reduce((a,b)=>a+b,0)/live.upload.length) : null;
    const liveDownMean = (live.download && live.download.length) ? (live.download.reduce((a,b)=>a+b,0)/live.download.length) : null;
    if(iters.length && iters[iters.length-1] === liveIter){ base[base.length-1]=liveBaseMean; up[up.length-1]=liveUpMean; down[down.length-1]=liveDownMean; }
    else { iters.push(liveIter); base.push(liveBaseMean); up.push(liveUpMean); down.push(liveDownMean); upThr.push(null); downThr.push(null); }
  }
  return { iterations: iters, baseline_mean: base, up_mean: up, down_mean: down, upload_mbps: upThr, download_mbps: downThr };
}

function updateCharts(history){
  createCharts();
  if(!history){ for(let k in charts){ charts[k].data.labels=[]; charts[k].data.datasets.forEach(ds=>ds.data=[]); charts[k].update(); } return; }
  const labels = history.iterations.map(i=>'#'+i);
  charts.baseline.data.labels=labels; charts.baseline.data.datasets[0].data=history.baseline_mean.map(v=>v===null?NaN:v); charts.baseline.update();
  charts.rtt.data.labels=labels; charts.rtt.data.datasets[0].data=history.up_mean.map(v=>v===null?NaN:v); charts.rtt.data.datasets[1].data=history.down_mean.map(v=>v===null?NaN:v); charts.rtt.update();
  charts.thr.data.labels=labels; charts.thr.data.datasets[0].data=history.upload_mbps.map(v=>v===null?NaN:v); charts.thr.data.datasets[1].data=history.download_mbps.map(v=>v===null?NaN:v); charts.thr.update();
}

function setSummary(locSummary, loc){ $('summaryLoc').innerText = loc||'—'; const base=(locSummary&&locSummary.ping_baseline_mean_stats)||{}; const up=(locSummary&&locSummary.ping_upload_mean_stats)||{}; const down=(locSummary&&locSummary.ping_download_mean_stats)||{}; const upThr=(locSummary&&locSummary.upload_stats_mbps)||{}; $('sb_baseline').innerText=(base.mean!==undefined)?`${base.mean} / ${base.median} / ${base.p95}`:'—'; $('sb_up').innerText=(up.mean!==undefined)?`${up.mean} / ${up.median} / ${up.p95}`:'—'; $('sb_down').innerText=(down.mean!==undefined)?`${down.mean} / ${down.median} / ${down.p95}`:'—'; $('sb_thr').innerText=((upThr.mean!==undefined)||(downThr.mean!==undefined))?`${upThr.mean||'—'} / ${downThr.mean||'—'}`:'—'; }

let sseConnected=false, es=null;
function startSSE(){
  if(typeof(EventSource)==='undefined'){ console.log('SSE not supported — fallback to polling'); return; }
  try{
    es=new EventSource('/stream');
    es.onopen=()=>{ sseConnected=true; console.log('SSE connected'); };
    es.onerror=e=>{ sseConnected=false; console.warn('SSE error', e); };
    es.onmessage=ev=>{
      try{
        const d=JSON.parse(ev.data);
        $('statusPill').innerText = d.running ? ('▶ ' + (d.current_location||'') + ' · iter ' + (d.current_iteration||0)) : 'idle';
        $('lastMsg').innerText = d.last_message || '(esperando...)';
        const logs=d.logs||[];
        if(logs.length===0) $('logbox').innerText='(no hay logs todavía)'; else { let txt=''; logs.slice(-6).reverse().forEach(it=>{ txt+=`[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n`; txt+=`  base:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`; }); $('logbox').innerText=txt; }
        const userSel=document.getElementById('locList').querySelector('.loc-btn.active');
        const viewLoc=userSel?userSel.dataset.loc:d.current_location;
        let history=d.current_location_history||null;
        const live=d.live||null;
        if(live && live.location && live.location === (viewLoc||d.current_location)){
          if(!history) history={iterations:[],baseline_mean:[],up_mean:[],down_mean:[],upload_mbps:[],download_mbps:[]};
          const merged=mergeHistoryWithLive(history, live);
          updateCharts(merged);
        } else {
          if(history) updateCharts(history);
          else {
            const items=(d.logs||[]).filter(it=>it.location===viewLoc);
            if(items.length){
              history={iterations:items.map(it=>it.iteration),baseline_mean:items.map(it=>(it.ping_stats_baseline||{}).mean_ms ?? null),up_mean:items.map(it=>(it.ping_stats_upload||{}).mean_ms ?? null),down_mean:items.map(it=>(it.ping_stats_download||{}).mean_ms ?? null),upload_mbps:items.map(it=>it.upload_mbps ?? null),download_mbps:items.map(it=>it.download_mbps ?? null)};
              updateCharts(history);
            } else updateCharts(null);
          }
        }
        const locSummary=(d.summary||{})[viewLoc]||d.current_location_summary||null;
        setSummary(locSummary, viewLoc);
      }catch(err){ console.error('SSE parse error', err); }
    };
  }catch(e){ console.warn('SSE init failed', e); sseConnected=false; }
}

async function updateStatus(){ if(sseConnected) return; try{ const res=await fetch('/status'); const d=await res.json(); $('statusPill').innerText = d.running ? ('▶ ' + (d.current_location||'') + ' · iter ' + (d.current_iteration||0)) : 'idle'; $('lastMsg').innerText = d.last_message || '(esperando...)'; const logs=d.logs||[]; if(logs.length===0) $('logbox').innerText='(no hay logs todavía)'; else { let txt=''; logs.slice(-6).reverse().forEach(it=>{ txt+=`[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n`; txt+=`  base:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`; }); $('logbox').innerText=txt; } const userSel=document.getElementById('locList').querySelector('.loc-btn.active'); let viewLoc=userSel?userSel.dataset.loc:d.current_location; if(!viewLoc){ const keys=Object.keys(d.summary||{}); if(keys.length) viewLoc=keys[keys.length-1]; } const locSummary=(d.summary||{})[viewLoc]||d.current_location_summary||null; let history=d.current_location_history||null; if(history && d.current_location===viewLoc) updateCharts(mergeHistoryWithLive(history, d.live||null)); else { const items=(d.logs||[]).filter(it=>it.location===viewLoc); if(items.length){ history={iterations:items.map(it=>it.iteration),baseline_mean:items.map(it=>(it.ping_stats_baseline||{}).mean_ms ?? null),up_mean:items.map(it=>(it.ping_stats_upload||{}).mean_ms ?? null),down_mean:items.map(it=>(it.ping_stats_download||{}).mean_ms ?? null),upload_mbps:items.map(it=>it.upload_mbps ?? null),download_mbps:items.map(it=>it.download_mbps ?? null)}; updateCharts(history);} else updateCharts(null);} setSummary(locSummary, viewLoc); }catch(e){console.error('poll failed', e);} }

$('startBtn').addEventListener('click', async ()=>{ const form=new FormData(); form.append('server',$('serverInput').value); form.append('iters',$('itersSelect').value); form.append('duration',$('durationInput').value); form.append('baseline',$('baselineInput').value); form.append('ping_interval',$('pingInterval').value); form.append('auto',$('autoChk').checked?'1':''); await fetch('/start',{method:'POST',body:form}).then(r=>r.json()).then(j=>{ if(j.error) alert('Error: '+j.error); updateStatus(); }).catch(e=>alert('start error: '+e)); });
$('pauseBtn').addEventListener('click', ()=> fetch('/pause',{method:'POST'}).then(()=>updateStatus()));
$('resumeBtn').addEventListener('click', ()=> fetch('/resume',{method:'POST'}).then(()=>updateStatus()));
$('stopBtn').addEventListener('click', ()=> fetch('/stop',{method:'POST'}).then(()=>updateStatus()));
$('jsonBtn').addEventListener('click', ()=> location.href='/download/json');
$('csvBtn').addEventListener('click', ()=> location.href='/download/csv');
$('clearAllBtn').addEventListener('click', async ()=>{ if(!confirm('¿Borrar todos los logs y resúmenes?')) return; const res=await fetch('/clear_all',{method:'POST'}); const j=await res.json(); if(j.ok){ alert('Todos los logs y resúmenes fueron eliminados.'); updateStatus(); } else alert('No fue posible limpiar: '+(j.error||'')); });
$('clearLocBtn').addEventListener('click', async ()=>{ const active=document.querySelector('.loc-btn.active'); if(!active){ alert('Selecciona primero una ubicación (p1..p8).'); return; } const loc=active.dataset.loc; if(!confirm(`¿Borrar logs y resumen para ${loc}?`)) return; const form=new FormData(); form.append('location',loc); const res=await fetch('/clear_location',{method:'POST',body:form}); const j=await res.json(); if(j.ok){ alert(`Logs y resumen de ${loc} eliminados.`); updateStatus(); } else alert('No fue posible limpiar: '+(j.error||'')); });

$('prevLoc').addEventListener('click', ()=>{ const btns=Array.from(document.querySelectorAll('.loc-btn')); const idx=btns.findIndex(b=>b.classList.contains('active')); const next=idx>0?btns[idx-1]:btns[btns.length-1]; next.click(); });
$('nextLoc').addEventListener('click', ()=>{ const btns=Array.from(document.querySelectorAll('.loc-btn')); const idx=btns.findIndex(b=>b.classList.contains('active')); const next=(idx<btns.length-1)?btns[idx+1]:btns[0]; next.click(); });

function selectLocation(l){ setActiveLocButton(l); updateStatus(); }

window.addEventListener('load', ()=>{ initLocButtons(); setActiveLocButton(LOCS[0]||''); createCharts(); startSSE(); setInterval(()=>{ if(!sseConnected) updateStatus(); }, 3000); });
</script>
</body>
</html>
"""

# inject locations
INDEX_HTML = INDEX_HTML.replace('@@LOCATIONS@@', json.dumps(LOCATIONS))

# ---------- Backend helpers ----------
def log(msg):
    ts = datetime.utcnow().isoformat() + 'Z'
    with state_lock:
        state['last_message'] = f"{ts} {msg}"
    print(state['last_message'], flush=True)

def safe_call(cmd, timeout=None):
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=timeout)
        return out.decode(errors='ignore')
    except subprocess.CalledProcessError as e:
        return e.output.decode(errors='ignore')
    except Exception:
        return ""

def get_rssi():
    try:
        out = safe_call(['termux-wifi-connectioninfo'])
        if not out:
            return None
        j = json.loads(out)
        for k in ('rssi','signalStrength','linkStrength','signal_strength'):
            if k in j:
                try:
                    return int(j[k])
                except:
                    pass
        if 'wifi' in j and isinstance(j['wifi'], dict):
            for k in ('rssi','signalStrength','linkStrength','signal_strength'):
                if k in j['wifi']:
                    try:
                        return int(j['wifi'][k])
                    except:
                        pass
    except Exception:
        return None
    return None

def run_ping_collect(host, duration_s=60, interval=0.2, append_to=None):
    import shutil
    rtts = []
    ping_exe = shutil.which('ping') or '/data/data/com.termux/files/usr/bin/ping'
    if not (ping_exe and os.path.isfile(ping_exe) and os.access(ping_exe, os.X_OK)):
        for p in ['/system/bin/ping', '/usr/bin/ping', '/bin/ping']:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                ping_exe = p
                break
    if not ping_exe or not os.path.isfile(ping_exe):
        log(f"run_ping_collect: 'ping' no encontrado. ping_exe={ping_exe}")
        return rtts
    cmd = [ping_exe, '-i', str(interval), '-w', str(int(duration_s)), host]
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except Exception:
        count = max(2, int(duration_s / interval))
        cmd = [ping_exe, '-i', str(interval), '-c', str(count), host]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except Exception as e:
            log(f"run_ping_collect: fallo al ejecutar ping fallback: {e}")
            return rtts
    start = time.time()
    stdout_chunks, stderr_chunks = [], []
    try:
        while True:
            line = proc.stdout.readline()
            if line:
                stdout_chunks.append(line)
                line_str = line.strip()
                if 'time=' in line_str:
                    try:
                        idx = line_str.rfind('time=')
                        token = line_str[idx+5:]
                        parts = token.split()
                        rtt = float(parts[0])
                        rtts.append(rtt)
                        if append_to is not None:
                            try: append_to.append(rtt)
                            except: pass
                    except: pass
            else:
                if proc.poll() is not None:
                    break
                if time.time() - start > duration_s + 10:
                    try: proc.kill()
                    except: pass
                    break
                time.sleep(0.03)
        try:
            se = proc.stderr.read()
            if se: stderr_chunks.append(se)
        except: pass
    except Exception as e:
        try:
            out, err = proc.communicate(timeout=2)
            if out: stdout_chunks.append(out)
            if err: stderr_chunks.append(err)
        except: pass
        log(f"run_ping_collect: excepción leyendo stdout/stderr: {e}")
    if not rtts:
        combined = ''.join(stdout_chunks) + '\n' + ''.join(stderr_chunks)
        snippet = combined[:4000] + ('...[truncated]' if len(combined) > 4000 else '')
        log(f"run_ping_collect: no se parsearon RTTs para host={host}. cmd={cmd} output_snippet={snippet}")
    return rtts

def percentile(sorted_list, p):
    if not sorted_list:
        return 0.0
    k = (len(sorted_list)-1) * (p/100.0)
    f = int(k)
    c = min(f+1, len(sorted_list)-1)
    if f == c:
        return sorted_list[int(k)]
    d0 = sorted_list[f] * (c - k)
    d1 = sorted_list[c] * (k - f)
    return d0 + d1

def compute_jitter_rfc3550(rtts):
    if not rtts or len(rtts) < 2:
        return 0.0
    J = 0.0
    for a,b in zip(rtts[:-1], rtts[1:]):
        D = b - a
        J += (abs(D) - J) / 16.0
    return J

def compute_ping_stats(rtts):
    if not rtts:
        return {"count":0}
    cnt = len(rtts)
    mean_v = mean(rtts)
    median_v = median(rtts)
    sorted_r = sorted(rtts)
    p95 = percentile(sorted_r, 95)
    diffs = [abs(j-i) for i,j in zip(rtts, rtts[1:])] if len(rtts) > 1 else []
    jitter_stats = {}
    if diffs:
        s = sorted(diffs)
        jitter_stats = {
            "count": len(diffs),
            "mean_ms": round(mean(diffs), 3),
            "median_ms": round(median(diffs), 3),
            "p95_ms": round(percentile(s, 95), 3),
            "min_ms": round(min(diffs),3),
            "max_ms": round(max(diffs),3)
        }
    else:
        jitter_stats = {"count":0}
    jitter_rfc = round(compute_jitter_rfc3550(rtts), 3)
    return {
        "count": cnt,
        "mean_ms": round(mean_v,3),
        "median_ms": round(median_v,3),
        "p95_ms": round(p95,3),
        "jitter_simple": jitter_stats,
        "jitter_rfc3550_ms": jitter_rfc
    }

def run_iperf3(server, duration=30, reverse=False):
    cmd = ['iperf3', '-c', server, '-J', '-t', str(duration)]
    if reverse:
        cmd.append('-R')
    out = safe_call(cmd, timeout=duration+25)
    if not out:
        return {"error": "no output"}
    try:
        return json.loads(out)
    except:
        return {"raw": out}

def parse_iperf_throughput(iperf_json):
    if not iperf_json:
        return None
    try:
        end = iperf_json.get('end', {})
        for k in ('sum_received','sum_sent'):
            s = end.get(k)
            if s and 'bits_per_second' in s:
                bps = float(s['bits_per_second'])
                return round(bps/1e6, 3)
        if 'intervals' in end and isinstance(end['intervals'], list):
            try:
                last = end['intervals'][-1]
                if 'sum' in last and 'bits_per_second' in last['sum']:
                    return round(last['sum']['bits_per_second']/1e6, 3)
            except:
                pass
        return None
    except:
        return None

def percentile_list(arr, p):
    if not arr: return 0.0
    s = sorted(arr)
    return round(percentile(s, p), 3)

def compute_aggregates_for_location(loc):
    items = [x for x in state['logs'] if x['location'] == loc]
    if not items:
        return {}
    def arr_of(key):
        return [it.get(key) for it in items if it.get(key) is not None]
    ping_baseline_means = [it.get('ping_stats_baseline', {}).get('mean_ms') for it in items if it.get('ping_stats_baseline', {}).get('mean_ms') is not None]
    ping_up_means = [it.get('ping_stats_upload', {}).get('mean_ms') for it in items if it.get('ping_stats_upload', {}).get('mean_ms') is not None]
    ping_down_means = [it.get('ping_stats_download', {}).get('mean_ms') for it in items if it.get('ping_stats_download', {}).get('mean_ms') is not None]
    download_vals = arr_of('download_mbps')
    upload_vals = arr_of('upload_mbps')
    def stats(arr):
        if not arr: return {}
        return {
            "mean": round(mean(arr),3),
            "median": round(median(arr),3),
            "p95": percentile_list(arr,95),
            "min": round(min(arr),3),
            "max": round(max(arr),3),
            "count": len(arr)
        }
    return {
        "ping_baseline_mean_stats": stats(ping_baseline_means),
        "ping_upload_mean_stats": stats(ping_up_means),
        "ping_download_mean_stats": stats(ping_down_means),
        "download_stats_mbps": stats(download_vals),
        "upload_stats_mbps": stats(upload_vals)
    }

# Runner thread (same logic previously described)
def runner_thread(server, iters_per_loc, duration_total, ping_interval=0.2, baseline_s=8, auto_advance=False):
    try:
        duration_total = int(duration_total)
        baseline_s = max(2, int(baseline_s))
        up_dur = duration_total // 2
        down_dur = duration_total - up_dur

        with state_lock:
            state['running'] = True
            state['paused'] = False
            state['stop_requested'] = False
            state['current_location_idx'] = 0
            state['current_iteration'] = 0
            state['logs'] = []
            state['summary'] = {}
            state['config'] = {
                "server": server,
                "iters_per_loc": iters_per_loc,
                "duration_total": duration_total,
                "upload_s": up_dur,
                "download_s": down_dur,
                "ping_interval": ping_interval,
                "baseline_s": baseline_s
            }
        log(f"Runner started server={server} iters={iters_per_loc} total_duration={duration_total}s (upload={up_dur}s download={down_dur}s) baseline={baseline_s}s ping_interval={ping_interval}s auto={auto_advance}")

        for loc_idx, loc in enumerate(LOCATIONS):
            if state['stop_requested']:
                break
            with state_lock:
                state['current_location_idx'] = loc_idx
            log(f"Starting location {loc} ({loc_idx+1}/{len(LOCATIONS)})")
            for it in range(1, iters_per_loc+1):
                if state['stop_requested']:
                    break
                while state['paused'] and not state['stop_requested']:
                    log(f"Paused at {loc} iteration {it}...")
                    time.sleep(1)
                with state_lock:
                    state['current_iteration'] = it
                    state['live']['location'] = loc
                    state['live']['iteration'] = it
                    state['live']['baseline'] = []
                    state['live']['upload'] = []
                    state['live']['download'] = []

                timestamp = datetime.utcnow().isoformat() + 'Z'
                rssi = get_rssi()
                log(f"[{loc}][iter {it}] RSSI: {rssi}")

                # Baseline
                with state_lock:
                    append_baseline = state['live']['baseline']
                ping_baseline_results = []
                ping_baseline_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=baseline_s, interval=ping_interval, append_to=append_baseline)), args=(ping_baseline_results,))
                ping_baseline_thread.start()
                ping_baseline_thread.join(timeout=baseline_s + 3)
                if ping_baseline_thread.is_alive():
                    try: ping_baseline_thread.join(timeout=1)
                    except: pass
                with state_lock:
                    final_baseline = list(state['live']['baseline'])
                ping_stats_baseline = compute_ping_stats(final_baseline)
                log(f"[{loc}][iter {it}] baseline ping count={ping_stats_baseline.get('count',0)} mean={ping_stats_baseline.get('mean_ms')}ms")

                # Upload
                with state_lock:
                    append_upload = state['live']['upload']
                ping_up_results = []
                ping_up_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=up_dur, interval=ping_interval, append_to=append_upload)), args=(ping_up_results,))
                ping_up_thread.start()
                log(f"[{loc}][iter {it}] iperf3 upload for {up_dur}s (ping upload running concurrently)")
                iperf_up = run_iperf3(server, duration=up_dur, reverse=False)
                upload_mbps = parse_iperf_throughput(iperf_up)
                log(f"[{loc}][iter {it}] upload Mbps: {upload_mbps}")
                if ping_up_thread.is_alive():
                    ping_up_thread.join(timeout=3)
                with state_lock:
                    final_upload = list(state['live']['upload'])

                # Download
                with state_lock:
                    append_download = state['live']['download']
                ping_down_results = []
                ping_down_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=down_dur, interval=ping_interval, append_to=append_download)), args=(ping_down_results,))
                ping_down_thread.start()
                log(f"[{loc}][iter {it}] iperf3 download (-R) for {down_dur}s (ping download running concurrently)")
                iperf_down = run_iperf3(server, duration=down_dur, reverse=True)
                download_mbps = parse_iperf_throughput(iperf_down)
                log(f"[{loc}][iter {it}] download Mbps: {download_mbps}")
                if ping_down_thread.is_alive():
                    ping_down_thread.join(timeout=3)
                with state_lock:
                    final_download = list(state['live']['download'])

                ping_stats_up = compute_ping_stats(final_upload)
                ping_stats_down = compute_ping_stats(final_download)
                log(f"[{loc}][iter {it}] ping(up) mean={ping_stats_up.get('mean_ms')}ms ping(down) mean={ping_stats_down.get('mean_ms')}ms")

                entry = {
                    "timestamp": timestamp,
                    "location": loc,
                    "iteration": it,
                    "rssi": rssi,
                    "baseline_seconds": baseline_s,
                    "upload_seconds": up_dur,
                    "download_seconds": down_dur,
                    "ping_samples_baseline_ms": final_baseline,
                    "ping_stats_baseline": ping_stats_baseline,
                    "ping_samples_upload_ms": final_upload,
                    "ping_stats_upload": ping_stats_up,
                    "ping_samples_download_ms": final_download,
                    "ping_stats_download": ping_stats_down,
                    "upload_mbps": upload_mbps,
                    "download_mbps": download_mbps,
                    "iperf_upload_raw": iperf_up,
                    "iperf_download_raw": iperf_down
                }
                with state_lock:
                    state['logs'].append(entry)
                    state['live']['baseline'] = []
                    state['live']['upload'] = []
                    state['live']['download'] = []

            with state_lock:
                state['summary'][loc] = compute_aggregates_for_location(loc)
            log(f"Finished location {loc}. aggregates updated.")
            if not auto_advance:
                with state_lock:
                    state['paused'] = True
                log(f"Paused after {loc}. Press Reanudar to continue.")
                while state['paused'] and not state['stop_requested']:
                    time.sleep(1)

        log("Runner finished.")
    except Exception as e:
        log(f"Runner exception: {e}")
    finally:
        with state_lock:
            state['running'] = False
            state['paused'] = False
            state['stop_requested'] = False
            state['current_location_idx'] = 0
            state['current_iteration'] = 0
            state['live']['location'] = None
            state['live']['iteration'] = None
            state['live']['baseline'] = []
            state['live']['upload'] = []
            state['live']['download'] = []

# ---------- SSE stream ----------
@APP.route('/stream')
def stream():
    def event_gen():
        while True:
            with state_lock:
                cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
                current_summary = state['summary'].get(cur_loc) if cur_loc else None
                history = None
                if cur_loc:
                    items = [x for x in state['logs'] if x['location'] == cur_loc]
                    if items:
                        iterations = [it.get('iteration') for it in items]
                        baseline_mean = [it.get('ping_stats_baseline', {}).get('mean_ms') for it in items]
                        up_mean = [it.get('ping_stats_upload', {}).get('mean_ms') for it in items]
                        down_mean = [it.get('ping_stats_download', {}).get('mean_ms') for it in items]
                        upload_mbps = [it.get('upload_mbps') for it in items]
                        download_mbps = [it.get('download_mbps') for it in items]
                        history = {
                            "iterations": iterations,
                            "baseline_mean": baseline_mean,
                            "up_mean": up_mean,
                            "down_mean": down_mean,
                            "upload_mbps": upload_mbps,
                            "download_mbps": download_mbps
                        }
                logs_slice = state['logs'][-10:]
                live = {
                    "location": state['live'].get('location'),
                    "iteration": state['live'].get('iteration'),
                    "baseline": state['live'].get('baseline')[-200:],
                    "upload": state['live'].get('upload')[-200:],
                    "download": state['live'].get('download')[-200:]
                }
                payload = {
                    "running": state['running'],
                    "paused": state['paused'],
                    "current_location": cur_loc,
                    "current_iteration": state['current_iteration'],
                    "last_message": state['last_message'],
                    "logs": logs_slice,
                    "summary": state['summary'],
                    "current_location_summary": current_summary,
                    "current_location_history": history,
                    "live": live,
                    "config": state.get('config', {})
                }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(0.5)
    return Response(stream_with_context(event_gen()), mimetype='text/event-stream')

# ---------- Routes: start/pause/resume/stop/status/clear/download ----------
@APP.route('/')
def index():
    return render_template_string(INDEX_HTML)

@APP.route('/start', methods=['POST'])
def start():
    if state['running']:
        return jsonify({"error":"already running"}), 400
    server = request.form.get('server') or (request.json.get('server') if request.json else None)
    if not server:
        return jsonify({"error":"server required"}), 400
    try: iters = int(request.form.get('iters', 5))
    except: iters = 5
    try: duration_total = int(request.form.get('duration', 60)); 
    except: duration_total = 60
    try: baseline_s = int(request.form.get('baseline', 8))
    except: baseline_s = 8
    try: ping_interval = float(request.form.get('ping_interval', 0.2))
    except: ping_interval = 0.2
    auto = request.form.get('auto') == '1'
    t = threading.Thread(target=runner_thread, args=(server, iters, duration_total, ping_interval, baseline_s, auto), daemon=True)
    t.start()
    return jsonify({"message":"started", "server": server, "iters": iters, "duration_total": duration_total, "baseline_s": baseline_s})

@APP.route('/pause', methods=['POST'])
def pause():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    with state_lock:
        state['paused'] = True
    return jsonify({"message":"paused"})

@APP.route('/resume', methods=['POST'])
def resume():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    with state_lock:
        state['paused'] = False
    return jsonify({"message":"resumed"})

@APP.route('/stop', methods=['POST'])
def stop():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    with state_lock:
        state['stop_requested'] = True
        state['paused'] = False
    return jsonify({"message":"stop requested"})

@APP.route('/clear_all', methods=['POST'])
def clear_all():
    with state_lock:
        state['logs'] = []
        state['summary'] = {}
    log("All logs and summaries cleared via UI.")
    return jsonify({"ok": True})

@APP.route('/clear_location', methods=['POST'])
def clear_location():
    loc = request.form.get('location') or request.args.get('location')
    if not loc:
        return jsonify({"error":"location parameter required"}), 400
    if loc not in LOCATIONS:
        return jsonify({"error": f"unknown location {loc}"}), 400
    with state_lock:
        before = len(state['logs'])
        state['logs'] = [entry for entry in state['logs'] if entry.get('location') != loc]
        if loc in state['summary']:
            del state['summary'][loc]
        after = len(state['logs'])
    log(f"Cleared logs for {loc} ({before-after} entries removed).")
    return jsonify({"ok": True, "removed": before-after})

@APP.route('/status', methods=['GET'])
def status():
    with state_lock:
        cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
        current_summary = state['summary'].get(cur_loc) if cur_loc else None
        history = None
        if cur_loc:
            items = [x for x in state['logs'] if x['location'] == cur_loc]
            if items:
                iterations = [it.get('iteration') for it in items]
                baseline_mean = [it.get('ping_stats_baseline', {}).get('mean_ms') for it in items]
                up_mean = [it.get('ping_stats_upload', {}).get('mean_ms') for it in items]
                down_mean = [it.get('ping_stats_download', {}).get('mean_ms') for it in items]
                upload_mbps = [it.get('upload_mbps') for it in items]
                download_mbps = [it.get('download_mbps') for it in items]
                history = {"iterations": iterations, "baseline_mean": baseline_mean, "up_mean": up_mean, "down_mean": down_mean, "upload_mbps": upload_mbps, "download_mbps": download_mbps}
        live_copy = {"location": state['live'].get('location'), "iteration": state['live'].get('iteration'), "baseline": state['live'].get('baseline', [])[-200:], "upload": state['live'].get('upload', [])[-200:], "download": state['live'].get('download', [])[-200:]}
        cfg = state.get('config', {})
        last_msg = state.get('last_message', '')
        logs_slice = state['logs'][-30:]
        summ = state['summary'].copy()
    return jsonify({"running": state['running'], "paused": state['paused'], "current_location": cur_loc, "current_iteration": state['current_iteration'], "last_message": last_msg, "logs": logs_slice, "summary": summ, "current_location_summary": current_summary, "current_location_history": history, "live": live_copy, "config": cfg})

@APP.route('/download/json')
def download_json():
    with state_lock:
        if not state['logs']:
            return jsonify({"error":"no logs yet"}), 400
        fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        out = {"generated_at": datetime.utcnow().isoformat()+'Z', "config": state.get('config',{}), "logs": state['logs'], "summary": state.get('summary',{})}
    with open(fn, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return send_file(fn, as_attachment=True)

@APP.route('/download/csv')
def download_csv():
    with state_lock:
        if not state['logs']:
            return jsonify({"error":"no logs yet"}), 400
        logs_copy = list(state['logs'])
    fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    fieldnames = ["timestamp","location","iteration","rssi","baseline_seconds","ping_baseline_count","ping_baseline_mean_ms","ping_baseline_median_ms","ping_baseline_p95_ms","ping_baseline_jitter_rfc3550_ms","ping_baseline_jitter_simple_mean_ms","upload_seconds","ping_upload_count","ping_upload_mean_ms","ping_upload_median_ms","ping_upload_p95_ms","ping_upload_jitter_rfc3550_ms","ping_upload_jitter_simple_mean_ms","download_seconds","ping_download_count","ping_download_mean_ms","ping_download_median_ms","ping_download_p95_ms","ping_download_jitter_rfc3550_ms","ping_download_jitter_simple_mean_ms","download_mbps","upload_mbps"]
    with open(fn, 'w', newline='', encoding='utf-8') as csvfile:
        w = csv.DictWriter(csvfile, fieldnames=fieldnames)
        w.writeheader()
        for it in logs_copy:
            ps_base = it.get('ping_stats_baseline', {})
            js_base = ps_base.get('jitter_simple', {})
            ps_up = it.get('ping_stats_upload', {})
            js_up = ps_up.get('jitter_simple', {})
            ps_down = it.get('ping_stats_download', {})
            js_down = ps_down.get('jitter_simple', {})
            w.writerow({
                "timestamp": it.get('timestamp'),
                "location": it.get('location'),
                "iteration": it.get('iteration'),
                "rssi": it.get('rssi'),
                "baseline_seconds": it.get('baseline_seconds'),
                "ping_baseline_count": ps_base.get('count',0),
                "ping_baseline_mean_ms": ps_base.get('mean_ms'),
                "ping_baseline_median_ms": ps_base.get('median_ms'),
                "ping_baseline_p95_ms": ps_base.get('p95_ms'),
                "ping_baseline_jitter_rfc3550_ms": ps_base.get('jitter_rfc3550_ms'),
                "ping_baseline_jitter_simple_mean_ms": js_base.get('mean_ms'),
                "upload_seconds": it.get('upload_seconds'),
                "ping_upload_count": ps_up.get('count',0),
                "ping_upload_mean_ms": ps_up.get('mean_ms'),
                "ping_upload_median_ms": ps_up.get('median_ms'),
                "ping_upload_p95_ms": ps_up.get('p95_ms'),
                "ping_upload_jitter_rfc3550_ms": ps_up.get('jitter_rfc3550_ms'),
                "ping_upload_jitter_simple_mean_ms": js_up.get('mean_ms'),
                "download_seconds": it.get('download_seconds'),
                "ping_download_count": ps_down.get('count',0),
                "ping_download_mean_ms": ps_down.get('mean_ms'),
                "ping_download_median_ms": ps_down.get('median_ms'),
                "ping_download_p95_ms": ps_down.get('p95_ms'),
                "ping_download_jitter_rfc3550_ms": ps_down.get('jitter_rfc3550_ms'),
                "ping_download_jitter_simple_mean_ms": js_down.get('mean_ms'),
                "download_mbps": it.get('download_mbps'),
                "upload_mbps": it.get('upload_mbps')
            })
    return send_file(fn, as_attachment=True)

# ---------- Diagnostic / 404 handler ----------
@APP.route('/routes', methods=['GET'])
def routes():
    out = []
    for rule in APP.url_map.iter_rules():
        methods = sorted([m for m in rule.methods if m not in ('HEAD', 'OPTIONS')])
        out.append({"rule": str(rule), "methods": methods})
    return jsonify({"routes": out})

@APP.errorhandler(404)
def handle_404(e):
    try:
        return render_template_string(INDEX_HTML), 200
    except Exception:
        return "not found", 404

# ---------- Run server ----------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Termux network tester — SSE live')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5000, type=int)
    args = parser.parse_args()
    print("Start: python run_tests.py --host 0.0.0.0 --port 5000")
    APP.run(host=args.host, port=args.port)