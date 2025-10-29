#!/usr/bin/env python3
"""
run_tests.py

Termux Network Tester — Mobile UI with WebSocket (Flask-SocketIO)

- Realtime updates via WebSocket (Socket.IO). Frontend subscribes to 'status' messages.
- Baseline ping + ping per phase (upload/download), iperf3 throughput.
- Forza IPv4 en ping, logs, export JSON/CSV.
- Clear all / clear per-location endpoints.
- Thread-safe state access (state_lock).
"""
import os
import subprocess
import threading
import time
import json
import csv
from datetime import datetime
from statistics import mean, median
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_socketio import SocketIO

APP = Flask(__name__)
# Initialize SocketIO using threading mode to avoid adding eventlet/gevent dependencies.
socketio = SocketIO(APP, async_mode='threading', cors_allowed_origins='*')

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
    "config": {}
}
state_lock = threading.Lock()

LOCATIONS = ["p1","p2","p3","p4","p5","p6","p7","p8"]

# ---------- Mobile UI template (marker @@LOCATIONS@@ replaced later) ----------
INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Termux Network Tester — WebSocket</title>
<style>
  :root{ --bg:#0f172a; --muted:#94a3b8; --accent:#60a5fa; --accent-2:#7dd3fc; --card-radius:12px; font-family:Inter,Roboto,Arial,sans-serif; color-scheme:dark }
  html,body{margin:0;height:100%;background:linear-gradient(180deg,var(--bg),#071029);color:#e6eef8}
  .app{max-width:900px;margin:0 auto;padding:12px}
  header{display:flex;align-items:center;gap:10px}
  .logo{width:44px;height:44px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:flex;align-items:center;justify-content:center;font-weight:700;color:#021028}
  h1{margin:0;font-size:1.05rem}
  .lead{color:var(--muted);font-size:0.88rem;margin-top:2px}
  main{display:flex;flex-direction:column;gap:12px;padding-bottom:92px}
  .card{background:rgba(255,255,255,0.03);border-radius:12px;padding:12px}
  input,select{background:transparent;border:1px solid rgba(255,255,255,0.06);padding:10px;border-radius:10px;color:inherit}
  .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  button{padding:10px 12px;border-radius:10px;border:0;font-weight:700}
  .primary{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028}
  .ghost{background:transparent;border:1px solid rgba(255,255,255,0.04);color:#dbeafe}
  .warn{background:linear-gradient(90deg,#f97316,#fb7185);color:#071023}
  .summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:8px}
  .s-card{padding:8px;border-radius:8px;background:rgba(255,255,255,0.02)}
  .s-val{font-weight:800}
  .chart{margin-top:8px;padding:8px;border-radius:8px;background:rgba(255,255,255,0.01)}
  canvas{width:100% !important;height:110px !important}
  .logbox{font-family:monospace;padding:8px;background:rgba(255,255,255,0.02);border-radius:8px;max-height:220px;overflow:auto}
  .bottom-bar{position:fixed;left:0;right:0;bottom:0;padding:8px 10px;background:linear-gradient(180deg, rgba(2,6,23,0.9), rgba(2,6,23,0.95));display:flex;gap:8px;align-items:center;justify-content:space-between}
  .loc-list{display:flex;gap:6px;overflow:auto}
  .loc-btn{min-width:44px;height:44px;border-radius:8px;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.02);color:#dbeafe;font-weight:700}
  .loc-btn.active{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028}
  @media (max-width:420px){ canvas{height:92px !important} }
</style>
</head>
<body>
<div class="app">
  <header><div class="logo">TN</div><div><h1>Termux Network Tester</h1><div class="lead">Actualizaciones en tiempo real (WebSocket)</div></div></header>

  <main>
    <section class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Controles</strong>
        <div id="statusPill" style="background:rgba(255,255,255,0.03);padding:8px;border-radius:999px;font-weight:700">idle</div>
      </div>

      <div style="margin-top:8px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input id="serverInput" type="text" value="192.168.1.100" placeholder="Servidor iperf3">
          <select id="itersSelect"><option>3</option><option>4</option><option selected>5</option></select>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
          <input id="durationInput" type="number" value="60" min="10" style="width:110px">
          <input id="baselineInput" type="number" value="8" min="2" max="30" style="width:110px">
          <input id="pingInterval" type="number" step="0.05" value="0.2" style="width:110px">
          <label style="color:#94a3b8"><input id="autoChk" type="checkbox"> Auto</label>
        </div>

        <div class="actions">
          <button class="primary" id="startBtn">▶ Iniciar</button>
          <button class="ghost" id="pauseBtn">⏸ Pausar</button>
          <button class="ghost" id="resumeBtn">⏯ Reanudar</button>
          <button class="warn" id="stopBtn">■ Detener</button>
          <button class="ghost" id="jsonBtn">⬇ JSON</button>
          <button class="ghost" id="csvBtn">⬇ CSV</button>
          <button class="ghost" id="clearLocBtn">🧹 Limpiar ubicación</button>
          <button class="ghost" id="clearAllBtn">🧼 Limpiar todo</button>
        </div>

        <div style="margin-top:8px;color:#94a3b8" id="lastMsg">(esperando...)</div>
      </div>
    </section>

    <section class="card">
      <div style="display:flex;justify-content:space-between">
        <strong>Resumen de ubicación</strong>
        <div id="summaryLoc" style="color:#94a3b8">—</div>
      </div>
      <div class="summary-grid" id="summaryGrid">
        <div class="s-card"><h4>Ping baseline</h4><div class="s-val" id="sb_baseline">—</div></div>
        <div class="s-card"><h4>Ping upload</h4><div class="s-val" id="sb_up">—</div></div>
        <div class="s-card"><h4>Ping download</h4><div class="s-val" id="sb_down">—</div></div>
        <div class="s-card"><h4>Throughput</h4><div class="s-val" id="sb_thr">—</div></div>
      </div>

      <div class="chart" style="margin-top:8px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <div style="flex:1;min-width:180px">
            <div style="color:#94a3b8">Baseline RTT (ms)</div>
            <canvas id="chartBaseline"></canvas>
          </div>
          <div style="flex:1;min-width:180px">
            <div style="color:#94a3b8">RTT upload / download (ms)</div>
            <canvas id="chartRTT"></canvas>
          </div>
        </div>
        <div style="margin-top:8px">
          <div style="color:#94a3b8">Throughput (Mbps)</div>
          <canvas id="chartThroughput"></canvas>
        </div>
      </div>
    </section>

    <section class="card">
      <strong>Logs recientes</strong>
      <div class="logbox" id="logbox">(no hay logs todavía)</div>
    </section>
  </main>
</div>

<div class="bottom-bar">
  <div class="loc-list" id="locList"></div>
  <div style="display:flex;gap:8px">
    <button id="prevLoc" class="loc-btn">◀</button>
    <button id="nextLoc" class="loc-btn">▶</button>
  </div>
</div>

<script src="/socket.io/socket.io.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const LOCS = @@LOCATIONS@@;
let socket = null;
let charts = { baseline:null, rtt:null, thr:null };

const $ = id => document.getElementById(id);

function initLocButtons(){
  const container = $('locList'); container.innerHTML = '';
  LOCS.forEach(l=>{
    const b = document.createElement('button'); b.className='loc-btn'; b.innerText = l.toUpperCase(); b.dataset.loc = l;
    b.addEventListener('click', ()=> { setActiveLocButton(l); requestStatus(); });
    container.appendChild(b);
  });
}
function setActiveLocButton(loc){
  const btns = document.querySelectorAll('.loc-btn'); btns.forEach(b=>b.classList.toggle('active', b.dataset.loc===loc));
  $('summaryLoc').innerText = loc || '—';
}
function createCharts(){
  if(charts.baseline) return;
  charts.baseline = new Chart($('chartBaseline').getContext('2d'), { type:'line', data:{labels:[],datasets:[{data:[],borderColor:'#60a5fa',backgroundColor:'rgba(96,165,250,0.08)',tension:0.3,pointRadius:0}]}, options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}}});
  charts.rtt = new Chart($('chartRTT').getContext('2d'), { type:'line', data:{labels:[],datasets:[{label:'upload',data:[],borderColor:'#34d399',pointRadius:0},{label:'download',data:[],borderColor:'#fb7185',pointRadius:0}]}, options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}}}});
  charts.thr = new Chart($('chartThroughput').getContext('2d'), { type:'line', data:{labels:[],datasets:[{label:'up',data:[],borderColor:'#a78bfa'},{label:'dn',data:[],borderColor:'#fb923c'}]}, options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}},scales:{y:{beginAtZero:true}}}});
}
function updateCharts(history){
  createCharts();
  if(!history){ ['baseline','rtt','thr'].forEach(k=>{ charts[k].data.labels=[]; charts[k].data.datasets.forEach(ds=>ds.data=[]); charts[k].update(); }); return; }
  const labels = history.iterations.map(i=>'#'+i);
  charts.baseline.data.labels = labels; charts.baseline.data.datasets[0].data = history.baseline_mean.map(v=>v===null?NaN:v); charts.baseline.update();
  charts.rtt.data.labels = labels; charts.rtt.data.datasets[0].data = history.up_mean.map(v=>v===null?NaN:v); charts.rtt.data.datasets[1].data = history.down_mean.map(v=>v===null?NaN:v); charts.rtt.update();
  charts.thr.data.labels = labels; charts.thr.data.datasets[0].data = history.upload_mbps.map(v=>v===null?NaN:v); charts.thr.data.datasets[1].data = history.download_mbps.map(v=>v===null?NaN:v); charts.thr.update();
}

function renderStatus(data){
  $('statusPill').innerText = data.running ? ('▶ ' + (data.current_location||'') + ' · iter ' + (data.current_iteration||0)) : 'idle';
  $('lastMsg').innerText = data.last_message || '(esperando...)';
  const logs = data.logs || [];
  if(logs.length===0) $('logbox').innerText='(no hay logs todavía)';
  else {
    let txt='';
    logs.slice(-6).reverse().forEach(it=>{ txt+=`[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'}Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n  base:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`; });
    $('logbox').innerText = txt;
  }
  // choose location to show
  const active = document.querySelector('.loc-btn.active');
  let viewLoc = active ? active.dataset.loc : data.current_location;
  if(!viewLoc){ const keys = Object.keys(data.summary||{}); if(keys.length) viewLoc = keys[keys.length-1]; }
  const locSummary = (data.summary||{})[viewLoc] || data.current_location_summary || null;
  if(locSummary){
    const base = locSummary.ping_baseline_mean_stats || {}; const up = locSummary.ping_upload_mean_stats||{}; const down = locSummary.ping_download_mean_stats||{}; const upThr = locSummary.upload_stats_mbps||{}; const downThr = locSummary.download_stats_mbps||{};
    $('sb_baseline').innerText = base.mean!==undefined ? `${base.mean} / ${base.median} / ${base.p95}` : '—';
    $('sb_up').innerText = up.mean!==undefined ? `${up.mean} / ${up.median} / ${up.p95}` : '—';
    $('sb_down').innerText = down.mean!==undefined ? `${down.mean} / ${down.median} / ${down.p95}` : '—';
    $('sb_thr').innerText = ((upThr.mean!==undefined)||(downThr.mean!==undefined)) ? `${upThr.mean||'—'} / ${downThr.mean||'—'}` : '—';
  } else {
    $('sb_baseline').innerText = $('sb_up').innerText = $('sb_down').innerText = $('sb_thr').innerText = '—';
  }
  // history for charts (backend gives current_location_history)
  const history = data.current_location_history || null;
  if(history) updateCharts(history);
  else updateCharts(null);
}

/* WebSocket connection */
function connectSocket(){
  socket = io();
  socket.on('connect', ()=>{ console.log('socket connected'); requestStatus(); });
  socket.on('status', (data)=>{ renderStatus(data); });
  socket.on('clear', ()=>{ requestStatus(); });
  socket.on('disconnect', ()=>{ console.log('socket disconnected'); });
}

/* request initial status via REST (fallback) */
async function requestStatus(){
  try{
    const res = await fetch('/status'); const data = await res.json(); renderStatus(data);
  }catch(e){ console.error('status request failed', e); }
}

/* actions (REST endpoints) */
$('startBtn').addEventListener('click', async ()=>{
  const form = new FormData();
  form.append('server',$('serverInput').value);
  form.append('iters',$('itersSelect').value);
  form.append('duration',$('durationInput').value);
  form.append('baseline',$('baselineInput').value);
  form.append('ping_interval',$('pingInterval').value);
  form.append('auto', $('autoChk').checked ? '1' : '');
  await fetch('/start',{method:'POST',body:form});
  // server will emit status via socket
});
$('pauseBtn').addEventListener('click', ()=> fetch('/pause',{method:'POST'}));
$('resumeBtn').addEventListener('click', ()=> fetch('/resume',{method:'POST'}));
$('stopBtn').addEventListener('click', ()=> fetch('/stop',{method:'POST'}));
$('jsonBtn').addEventListener('click', ()=> location.href='/download/json');
$('csvBtn').addEventListener('click', ()=> location.href='/download/csv');

$('clearAllBtn').addEventListener('click', async ()=>{
  if(!confirm('¿Borrar todos los logs y resúmenes?')) return;
  await fetch('/clear_all',{method:'POST'});
  // server emits 'clear' and status
});
$('clearLocBtn').addEventListener('click', async ()=>{
  const active = document.querySelector('.loc-btn.active'); if(!active){ alert('Selecciona una ubicación'); return; }
  const loc = active.dataset.loc; if(!confirm(`Borrar logs para ${loc}?`)) return;
  const form = new FormData(); form.append('location', loc);
  await fetch('/clear_location',{method:'POST', body: form});
});

$('prevLoc').addEventListener('click', ()=> { const btns = Array.from(document.querySelectorAll('.loc-btn')); const idx = btns.findIndex(b=>b.classList.contains('active')); const next = idx>0?btns[idx-1]:btns[btns.length-1]; next.click(); });
$('nextLoc').addEventListener('click', ()=> { const btns = Array.from(document.querySelectorAll('.loc-btn')); const idx = btns.findIndex(b=>b.classList.contains('active')); const next = (idx<btns.length-1)?btns[idx+1]:btns[0]; next.click(); });

window.addEventListener('load', ()=>{ initLocButtons(); setActiveLocButton(LOCS[0]); createCharts(); connectSocket(); setInterval(()=>{ if(!socket || !socket.connected) requestStatus(); }, 8000); });
</script>
</body>
</html>
"""

# Safely replace marker with JSON list of locations
INDEX_HTML = INDEX_HTML.replace('@@LOCATIONS@@', json.dumps(LOCATIONS))

# ---------- Backend measurement logic (same as before) ----------

def log(msg):
    ts = datetime.utcnow().isoformat() + 'Z'
    with state_lock:
        state['last_message'] = f"{ts} {msg}"
    print(state['last_message'], flush=True)
    # also emit updated status to clients
    emit_state()

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

def run_ping_collect(host, duration_s=60, interval=0.2):
    rtts = []
    cmd = ['ping', '-4', '-i', str(interval), '-w', str(int(duration_s)), host]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except Exception:
        count = max(2, int(duration_s / interval))
        cmd = ['ping', '-4', '-i', str(interval), '-c', str(count), host]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    start = time.time()
    while True:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
            if time.time() - start > duration_s + 5:
                try: proc.kill()
                except: pass
                break
            continue
        line = line.strip()
        if 'time=' in line:
            try:
                idx = line.rfind('time=')
                token = line[idx+5:]
                parts = token.split()
                rtt = float(parts[0])
                rtts.append(rtt)
            except Exception:
                pass
        if time.time() - start > duration_s:
            time.sleep(0.05)
            try:
                proc.terminate()
            except:
                pass
            break
    try:
        proc.wait(timeout=1)
    except:
        try: proc.kill()
        except: pass
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

def emit_state():
    """
    Build a snapshot of the state and emit via Socket.IO to all connected clients.
    """
    with state_lock:
        cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
        current_summary = state['summary'].get(cur_loc) if cur_loc else None
        # build history for current location
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
        snapshot = {
            "running": state['running'],
            "paused": state['paused'],
            "current_location": cur_loc,
            "current_iteration": state['current_iteration'],
            "last_message": state.get('last_message'),
            "logs": state['logs'][-30:],
            "summary": state['summary'],
            "current_location_summary": current_summary,
            "current_location_history": history,
            "config": state.get('config', {})
        }
    # broadcast to all connected clients
    try:
        socketio.emit('status', snapshot, broadcast=True)
    except Exception:
        pass

# Runner thread (emits state at key points)
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
        emit_state()
        log(f"Runner started server={server} iters={iters_per_loc} total_duration={duration_total}s (upload={up_dur}s download={down_dur}s) baseline={baseline_s}s ping_interval={ping_interval}s auto={auto_advance}")

        for loc_idx, loc in enumerate(LOCATIONS):
            with state_lock:
                if state['stop_requested']:
                    break
                state['current_location_idx'] = loc_idx
            log(f"Starting location {loc} ({loc_idx+1}/{len(LOCATIONS)})")
            emit_state()
            for it in range(1, iters_per_loc+1):
                with state_lock:
                    if state['stop_requested']:
                        break
                    # wait if paused
                while True:
                    with state_lock:
                        if not state['paused']:
                            break
                        if state['stop_requested']:
                            break
                    time.sleep(0.5)

                with state_lock:
                    state['current_iteration'] = it
                emit_state()

                timestamp = datetime.utcnow().isoformat() + 'Z'
                rssi = get_rssi()
                log(f"[{loc}][iter {it}] RSSI: {rssi}")

                # baseline
                ping_baseline_results = []
                log(f"[{loc}][iter {it}] Running baseline ping for {baseline_s}s")
                ping_baseline_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=baseline_s, interval=ping_interval)), args=(ping_baseline_results,))
                ping_baseline_thread.start()
                ping_baseline_thread.join(timeout=baseline_s + 3)
                ping_baseline = ping_baseline_results if isinstance(ping_baseline_results, list) else []
                ping_stats_baseline = compute_ping_stats(ping_baseline)
                log(f"[{loc}][iter {it}] baseline mean={ping_stats_baseline.get('mean_ms')}ms")
                emit_state()

                # upload phase
                ping_up_results = []
                ping_up_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=up_dur, interval=ping_interval)), args=(ping_up_results,))
                ping_up_thread.start()
                log(f"[{loc}][iter {it}] iperf3 upload for {up_dur}s")
                iperf_up = run_iperf3(server, duration=up_dur, reverse=False)
                upload_mbps = parse_iperf_throughput(iperf_up)
                if ping_up_thread.is_alive():
                    ping_up_thread.join(timeout=3)
                log(f"[{loc}][iter {it}] upload Mbps: {upload_mbps}")
                emit_state()

                # download phase
                ping_down_results = []
                ping_down_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=down_dur, interval=ping_interval)), args=(ping_down_results,))
                ping_down_thread.start()
                log(f"[{loc}][iter {it}] iperf3 download for {down_dur}s")
                iperf_down = run_iperf3(server, duration=down_dur, reverse=True)
                download_mbps = parse_iperf_throughput(iperf_down)
                if ping_down_thread.is_alive():
                    ping_down_thread.join(timeout=3)
                log(f"[{loc}][iter {it}] download Mbps: {download_mbps}")
                emit_state()

                rtts_up = ping_up_results if isinstance(ping_up_results, list) else []
                rtts_down = ping_down_results if isinstance(ping_down_results, list) else []
                ping_stats_up = compute_ping_stats(rtts_up)
                ping_stats_down = compute_ping_stats(rtts_down)

                entry = {
                    "timestamp": timestamp,
                    "location": loc,
                    "iteration": it,
                    "rssi": rssi,
                    "baseline_seconds": baseline_s,
                    "upload_seconds": up_dur,
                    "download_seconds": down_dur,
                    "ping_samples_baseline_ms": ping_baseline,
                    "ping_stats_baseline": ping_stats_baseline,
                    "ping_samples_upload_ms": rtts_up,
                    "ping_stats_upload": ping_stats_up,
                    "ping_samples_download_ms": rtts_down,
                    "ping_stats_download": ping_stats_down,
                    "upload_mbps": upload_mbps,
                    "download_mbps": download_mbps,
                    "iperf_upload_raw": iperf_up,
                    "iperf_download_raw": iperf_down
                }
                with state_lock:
                    state['logs'].append(entry)
                emit_state()

            # compute aggregates for location and expose them to UI
            with state_lock:
                state['summary'][loc] = compute_aggregates_for_location(loc)
            log(f"Finished location {loc}. aggregates updated.")
            emit_state()

            if not auto_advance:
                with state_lock:
                    state['paused'] = True
                log(f"Paused after {loc}.")
                # wait until resumed
                while True:
                    with state_lock:
                        if not state['paused'] or state['stop_requested']:
                            break
                    time.sleep(0.5)

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
        emit_state()

# ---------- Flask routes ----------

@APP.route('/')
def index():
    return render_template_string(INDEX_HTML)

@APP.route('/start', methods=['POST'])
def start():
    with state_lock:
        if state['running']:
            return jsonify({"error":"already running"}), 400
    server = request.form.get('server')
    if not server:
        return jsonify({"error":"server required"}), 400
    try:
        iters = int(request.form.get('iters', 5))
    except:
        iters = 5
    try:
        duration_total = int(request.form.get('duration', 60))
        if duration_total < 10:
            duration_total = 60
    except:
        duration_total = 60
    try:
        baseline_s = int(request.form.get('baseline', 8))
        if baseline_s < 2:
            baseline_s = 2
    except:
        baseline_s = 8
    try:
        ping_interval = float(request.form.get('ping_interval', 0.2))
        if ping_interval <= 0: ping_interval = 0.2
    except:
        ping_interval = 0.2
    auto = request.form.get('auto') == '1'
    t = threading.Thread(target=runner_thread, args=(server, iters, duration_total, ping_interval, baseline_s, auto), daemon=True)
    t.start()
    return jsonify({"message":"started"})

@APP.route('/pause', methods=['POST'])
def pause():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['paused'] = True
    emit_state()
    return jsonify({"message":"paused"})

@APP.route('/resume', methods=['POST'])
def resume():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['paused'] = False
    emit_state()
    return jsonify({"message":"resumed"})

@APP.route('/stop', methods=['POST'])
def stop():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['stop_requested'] = True
        state['paused'] = False
    emit_state()
    return jsonify({"message":"stop requested"})

@APP.route('/clear_all', methods=['POST'])
def clear_all():
    with state_lock:
        state['logs'] = []
        state['summary'] = {}
    log("All logs and summaries cleared via UI.")
    # emit clear event
    try:
        socketio.emit('clear', {'ok': True}, broadcast=True)
    except:
        pass
    emit_state()
    return jsonify({"ok": True})

@APP.route('/clear_location', methods=['POST'])
def clear_location():
    loc = request.form.get('location')
    if not loc:
        return jsonify({"error":"location required"}), 400
    if loc not in LOCATIONS:
        return jsonify({"error":"unknown location"}), 400
    with state_lock:
        before = len(state['logs'])
        state['logs'] = [entry for entry in state['logs'] if entry.get('location') != loc]
        if loc in state['summary']:
            del state['summary'][loc]
        after = len(state['logs'])
    log(f"Cleared logs for {loc} ({before-after} entries removed).")
    emit_state()
    return jsonify({"ok": True, "removed": before-after})

@APP.route('/status', methods=['GET'])
def status():
    # keep REST status for compatibility
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
        snapshot = {
            "running": state['running'],
            "paused": state['paused'],
            "current_location": cur_loc,
            "current_iteration": state['current_iteration'],
            "last_message": state.get('last_message'),
            "logs": state['logs'][-30:],
            "summary": state['summary'],
            "current_location_summary": current_summary,
            "current_location_history": history,
            "config": state.get('config', {})
        }
    return jsonify(snapshot)

@APP.route('/download/json')
def download_json():
    with state_lock:
        if not state['logs']:
            return jsonify({"error":"no logs yet"}), 400
        fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        with open(fn, 'w', encoding='utf-8') as f:
            out = {"generated_at": datetime.utcnow().isoformat()+'Z', "config": state.get('config',{}), "logs": state['logs'], "summary": state.get('summary',{})}
            json.dump(out, f, indent=2, ensure_ascii=False)
    return send_file(fn, as_attachment=True)

@APP.route('/download/csv')
def download_csv():
    with state_lock:
        if not state['logs']:
            return jsonify({"error":"no logs yet"}), 400
        fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
        fieldnames = [
            "timestamp","location","iteration","rssi",
            "baseline_seconds","ping_baseline_count","ping_baseline_mean_ms","ping_baseline_median_ms","ping_baseline_p95_ms","ping_baseline_jitter_rfc3550_ms","ping_baseline_jitter_simple_mean_ms",
            "upload_seconds","ping_upload_count","ping_upload_mean_ms","ping_upload_median_ms","ping_upload_p95_ms","ping_upload_jitter_rfc3550_ms","ping_upload_jitter_simple_mean_ms",
            "download_seconds","ping_download_count","ping_download_mean_ms","ping_download_median_ms","ping_download_p95_ms","ping_download_jitter_rfc3550_ms","ping_download_jitter_simple_mean_ms",
            "download_mbps","upload_mbps"
        ]
        with open(fn, 'w', newline='', encoding='utf-8') as csvfile:
            w = csv.DictWriter(csvfile, fieldnames=fieldnames)
            w.writeheader()
            for it in state['logs']:
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

# ---------- Socket.IO events ----------
@socketio.on('connect')
def on_connect():
    # send current state on new connection
    emit_state()

@socketio.on('disconnect')
def on_disconnect():
    pass

# ---------- Run server ----------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Termux network tester — WebSocket UI')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5000, type=int)
    args = parser.parse_args()
    print("Start: python run_tests.py --host 0.0.0.0 --port 5000")
    # Run via SocketIO (wraps Flask)
    socketio.run(APP, host=args.host, port=args.port)