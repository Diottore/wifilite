#!/usr/bin/env python3
"""
run_tests.py

Termux Network Tester — Mobile UI with WebSocket (Flask-SocketIO)

- Real-time updates via WebSocket (Socket.IO) instead of polling.
- Mobile-first UI, baseline + ping per phase, iperf3 throughput.
- Clear controls for per-location or whole-log clearing.
- Thread-safe state via a threading.Lock.
- Requires: pip install Flask flask-socketio eventlet
  and Termux packages: pkg install python iperf3 termux-api

Run:
  python run_tests.py --host 0.0.0.0 --port 5000
Open:
  http://localhost:5000 on the phone (or http://<phone-ip>:5000 from LAN)

Notes:
- This uses eventlet for async support. If your Termux environment lacks eventlet,
  install it with pip. If you prefer gevent, adapt socketio initialization accordingly.
"""
import eventlet
# apply monkey patching early for socket compatibility
eventlet.monkey_patch()

import os
import subprocess
import threading
import time
import json
import csv
from datetime import datetime
from statistics import mean, median
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_socketio import SocketIO, emit

APP = Flask(__name__)
# allow large payloads if needed
APP.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

# Use eventlet async mode
socketio = SocketIO(APP, async_mode='eventlet', cors_allowed_origins="*")

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

# ---------- HTML template (mobile) ----------
# Use @@LOCATIONS@@ marker to insert JSON safely.
INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Termux Network Tester — WebSocket</title>
<style>
  :root{
    --bg:#0f172a; --muted:#94a3b8; --accent:#60a5fa; --accent-2:#7dd3fc;
    --card-radius:12px; --btn-h:50px; font-family:Inter,Roboto,Arial,sans-serif;
  }
  html,body{height:100%;margin:0;background:linear-gradient(180deg,var(--bg),#071029);color:#e6eef8}
  .app{max-width:900px;margin:0 auto;padding:12px}
  header{display:flex;gap:10px;align-items:center}
  .logo{width:44px;height:44px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:flex;align-items:center;justify-content:center;font-weight:700;color:#021028}
  h1{margin:0;font-size:1.05rem}
  .lead{color:var(--muted);font-size:0.9rem;margin-top:2px}
  main{display:flex;flex-direction:column;gap:12px;padding-bottom:86px}
  .card{background:rgba(255,255,255,0.02);border-radius:12px;padding:12px}
  input,select{padding:10px;border-radius:10px;border:1px solid rgba(255,255,255,0.04);background:transparent;color:inherit}
  .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  button{height:var(--btn-h);border-radius:10px;padding:0 12px;font-weight:700}
  .primary{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028;border:0}
  .ghost{background:transparent;border:1px solid rgba(255,255,255,0.04);color:#dbeafe}
  .warn{background:linear-gradient(90deg,#fb7185,#f97316);color:#021028;border:0}
  .pill{background:rgba(255,255,255,0.03);padding:8px 10px;border-radius:999px;font-weight:700}
  .summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:8px}
  .s-card{padding:8px;border-radius:8px;background:rgba(255,255,255,0.01)}
  canvas{width:100% !important;height:110px !important}
  .logbox{font-family:monospace;background:rgba(255,255,255,0.02);padding:8px;border-radius:8px;max-height:220px;overflow:auto}
  .bottom-bar{position:fixed;left:0;right:0;bottom:0;padding:10px;background:rgba(2,6,23,0.95);display:flex;gap:8px;align-items:center;justify-content:space-between}
  .loc-btn{min-width:40px;height:40px;border-radius:8px;background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.02);color:#dbeafe;font-weight:700}
  .loc-btn.active{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#021028}
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">TN</div>
    <div><h1>Termux Network Tester</h1><div class="lead">WebSocket real-time • p1..p8</div></div>
  </header>

  <main>
    <section class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Controles</strong>
        <div class="pill" id="statusPill">idle</div>
      </div>

      <div style="margin-top:10px">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <input id="serverInput" type="text" value="192.168.1.100" placeholder="iperf3 server">
          <select id="itersSelect"><option>3</option><option>4</option><option selected>5</option></select>
        </div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <input id="durationInput" type="number" value="60" min="10" style="width:120px">
          <input id="baselineInput" type="number" value="8" min="2" max="30" style="width:110px">
          <input id="pingInterval" type="number" step="0.05" value="0.2" style="width:110px">
          <label style="color:var(--muted);display:flex;align-items:center;gap:6px"><input id="autoChk" type="checkbox"> Auto</label>
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

        <div style="margin-top:8px"><div id="lastMsg" style="color:var(--muted)">(esperando...)</div></div>
      </div>
    </section>

    <section class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Resumen de ubicación</strong>
        <div id="summaryLoc" style="color:var(--muted)">—</div>
      </div>

      <div class="summary-grid" id="summaryGrid">
        <div class="s-card"><h4>Baseline</h4><div id="sb_baseline">—</div></div>
        <div class="s-card"><h4>Upload RTT</h4><div id="sb_up">—</div></div>
        <div class="s-card"><h4>Download RTT</h4><div id="sb_down">—</div></div>
        <div class="s-card"><h4>Throughput</h4><div id="sb_thr">—</div></div>
      </div>

      <div style="margin-top:8px" class="chart">
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <div style="flex:1;min-width:160px"><div style="color:var(--muted)">Baseline RTT</div><canvas id="chartBaseline"></canvas></div>
          <div style="flex:1;min-width:160px"><div style="color:var(--muted)">RTT Upload/Download</div><canvas id="chartRTT"></canvas></div>
        </div>
        <div style="margin-top:8px"><div style="color:var(--muted)">Throughput</div><canvas id="chartThroughput"></canvas></div>
      </div>
    </section>

    <section class="card">
      <strong>Logs recientes</strong>
      <div class="logbox" id="logbox">(no hay logs todavía)</div>
    </section>
  </main>
</div>

<div class="bottom-bar">
  <div id="locList" style="display:flex;gap:6px;overflow:auto"></div>
  <div style="display:flex;gap:6px">
    <button id="prevLoc" class="loc-btn">◀</button>
    <button id="nextLoc" class="loc-btn">▶</button>
  </div>
</div>

<!-- Socket.IO client -->
<script src="/socket.io/socket.io.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const LOCS = @@LOCATIONS@@;
let socket = null;
let charts = { baseline:null, rtt:null, thr:null };

function $(id){ return document.getElementById(id); }

function initLocButtons(){
  const container = $('locList');
  container.innerHTML = '';
  LOCS.forEach(l=>{
    const b = document.createElement('button');
    b.className = 'loc-btn';
    b.textContent = l.toUpperCase();
    b.dataset.loc = l;
    b.onclick = ()=> { selectLocation(l); };
    container.appendChild(b);
  });
  // select first by default
  selectLocation(LOCS[0]);
}

function setActiveLocButton(loc){
  document.querySelectorAll('.loc-btn').forEach(b=> b.classList.toggle('active', b.dataset.loc===loc));
  $('summaryLoc').innerText = loc || '—';
}

function createCharts(){
  if(charts.baseline) return;
  charts.baseline = new Chart($('chartBaseline').getContext('2d'), { type:'line',
    data:{ labels:[], datasets:[{data:[], borderColor:'#60a5fa', backgroundColor:'rgba(96,165,250,0.08)', tension:0.3, pointRadius:0}]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{display:false}}}
  });
  charts.rtt = new Chart($('chartRTT').getContext('2d'), { type:'line',
    data:{ labels:[], datasets:[
      {label:'upload', data:[], borderColor:'#34d399', pointRadius:0},
      {label:'download', data:[], borderColor:'#fb7185', pointRadius:0}
    ]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}}, scales:{x:{display:false}}}
  });
  charts.thr = new Chart($('chartThroughput').getContext('2d'), { type:'line',
    data:{ labels:[], datasets:[
      {label:'upload', data:[], borderColor:'#a78bfa', pointRadius:0},
      {label:'download', data:[], borderColor:'#fb923c', pointRadius:0}
    ]},
    options:{responsive:true, maintainAspectRatio:false, plugins:{legend:{position:'bottom'}}, scales:{x:{display:false}}}
  });
}

function updateUIFromStatus(data){
  $('statusPill').innerText = data.running ? ('▶ ' + (data.current_location||'') + ' · iter ' + (data.current_iteration||0)) : 'idle';
  $('lastMsg').innerText = data.last_message || '';
  // logs
  if((data.logs||[]).length===0) $('logbox').innerText='(no hay logs todavía)';
  else {
    let s = '';
    data.logs.slice(-6).reverse().forEach(it=>{
      s += `[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n`;
      s += `  base:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`;
    });
    $('logbox').innerText = s;
  }
  // decide view location
  const active = document.querySelector('.loc-btn.active');
  const viewLoc = active ? active.dataset.loc : data.current_location;
  const locSummary = (data.summary||{})[viewLoc] || data.current_location_summary || null;
  if(locSummary){
    const base = locSummary.ping_baseline_mean_stats || {};
    const up = locSummary.ping_upload_mean_stats || {};
    const down = locSummary.ping_download_mean_stats || {};
    const upThr = locSummary.upload_stats_mbps || {};
    $('sb_baseline').innerText = base.mean!==undefined ? `${base.mean} / ${base.median} / ${base.p95}` : '—';
    $('sb_up').innerText = up.mean!==undefined ? `${up.mean} / ${up.median} / ${up.p95}` : '—';
    $('sb_down').innerText = down.mean!==undefined ? `${down.mean} / ${down.median} / ${down.p95}` : '—';
    $('sb_thr').innerText = `${upThr.mean||'—'} / ${ (locSummary.download_stats_mbps && locSummary.download_stats_mbps.mean) || '—' }`;
  }
  // history for charts (use current_location_history or build)
  const hist = data.current_location_history || null;
  if(hist){
    createCharts();
    const labels = hist.iterations.map(i=>`#${i}`);
    charts.baseline.data.labels = labels; charts.baseline.data.datasets[0].data = hist.baseline_mean.map(v=> v===null?NaN:v); charts.baseline.update();
    charts.rtt.data.labels = labels; charts.rtt.data.datasets[0].data = hist.up_mean.map(v=> v===null?NaN:v); charts.rtt.data.datasets[1].data = hist.down_mean.map(v=> v===null?NaN:v); charts.rtt.update();
    charts.thr.data.labels = labels; charts.thr.data.datasets[0].data = hist.upload_mbps.map(v=> v===null?NaN:v); charts.thr.data.datasets[1].data = hist.download_mbps.map(v=> v===null?NaN:v); charts.thr.update();
  }
}

/* Socket.IO connection */
function initSocket(){
  socket = io();
  socket.on('connect', ()=> { console.log('socket connected'); });
  socket.on('status', (data)=> { updateUIFromStatus(data); });
  socket.on('log', (msg)=> { /* optional small logs */ console.log('log',msg); });
  socket.on('disconnect', ()=> { console.log('socket disconnected'); });
  // request initial status
  socket.emit('get_status');
}

/* user actions */
$('startBtn').onclick = async ()=> {
  const form = new FormData();
  form.append('server', $('serverInput').value);
  form.append('iters', $('itersSelect').value);
  form.append('duration', $('durationInput').value);
  form.append('baseline', $('baselineInput').value);
  form.append('ping_interval', $('pingInterval').value);
  form.append('auto', $('autoChk').checked ? '1' : '');
  const res = await fetch('/start', { method:'POST', body: form });
  const j = await res.json();
  if(j.error) alert('Error: '+j.error);
};
$('pauseBtn').onclick = ()=> fetch('/pause',{method:'POST'});
$('resumeBtn').onclick = ()=> fetch('/resume',{method:'POST'});
$('stopBtn').onclick = ()=> fetch('/stop',{method:'POST'});
$('jsonBtn').onclick = ()=> window.location='/download/json';
$('csvBtn').onclick = ()=> window.location='/download/csv';

$('clearAllBtn').onclick = async ()=>{
  if(!confirm('¿Borrar todos los logs y resúmenes?')) return;
  const r = await fetch('/clear_all', { method:'POST' }); const j = await r.json();
  if(j.ok) alert('Se borró todo');
};
$('clearLocBtn').onclick = async ()=>{
  const active = document.querySelector('.loc-btn.active'); if(!active){ alert('Selecciona ubicación'); return; }
  const loc = active.dataset.loc;
  if(!confirm(`¿Borrar logs de ${loc}?`)) return;
  const form = new FormData(); form.append('location', loc);
  const r = await fetch('/clear_location', { method:'POST', body: form }); const j = await r.json();
  if(j.ok) alert(`Se borró ${loc}`);
};

$('prevLoc').onclick = ()=>{
  const btns = Array.from(document.querySelectorAll('.loc-btn')); const idx = btns.findIndex(b=>b.classList.contains('active'));
  const next = idx>0 ? btns[idx-1] : btns[btns.length-1]; next.click();
};
$('nextLoc').onclick = ()=>{
  const btns = Array.from(document.querySelectorAll('.loc-btn')); const idx = btns.findIndex(b=>b.classList.contains('active'));
  const next = (idx < btns.length-1) ? btns[idx+1] : btns[0]; next.click();
};

function selectLocation(l){
  setActiveLocButton(l);
  // request server to emit status (optional)
  if(socket) socket.emit('get_status_for', { location: l });
}

/* init */
window.addEventListener('load', ()=>{
  initLocButtons();
  createCharts();
  initSocket();
});
</script>
</body>
</html>
"""

# Inject locations safely
INDEX_HTML = INDEX_HTML.replace('@@LOCATIONS@@', json.dumps(LOCATIONS))

# ---------- Backend: helper functions ----------

def log(msg):
    ts = datetime.utcnow().isoformat() + 'Z'
    with state_lock:
        state['last_message'] = f"{ts} {msg}"
    # broadcast small log message
    socketio.emit('log', {'msg': state['last_message']})
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

def run_ping_collect(host, duration_s=60, interval=0.2):
    rtts = []
    # force IPv4
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
            try: proc.terminate()
            except: pass
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
    f = int(k); c = min(f+1, len(sorted_list)-1)
    if f == c:
        return sorted_list[int(k)]
    d0 = sorted_list[f] * (c - k); d1 = sorted_list[c] * (k - f)
    return d0 + d1

def compute_jitter_rfc3550(rtts):
    if not rtts or len(rtts)<2: return 0.0
    J = 0.0
    for a,b in zip(rtts[:-1], rtts[1:]):
        D = b - a
        J += (abs(D) - J) / 16.0
    return J

def compute_ping_stats(rtts):
    if not rtts:
        return {"count":0}
    cnt = len(rtts)
    mean_v = mean(rtts); median_v = median(rtts)
    sorted_r = sorted(rtts); p95 = percentile(sorted_r, 95)
    diffs = [abs(j-i) for i,j in zip(rtts, rtts[1:])] if len(rtts)>1 else []
    jitter_stats = {}
    if diffs:
        s = sorted(diffs)
        jitter_stats = {
            "count": len(diffs),
            "mean_ms": round(mean(diffs),3),
            "median_ms": round(median(diffs),3),
            "p95_ms": round(percentile(s,95),3),
            "min_ms": round(min(diffs),3),
            "max_ms": round(max(diffs),3)
        }
    else:
        jitter_stats = {"count":0}
    jitter_rfc = round(compute_jitter_rfc3550(rtts),3)
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
    if reverse: cmd.append('-R')
    out = safe_call(cmd, timeout=duration+25)
    if not out: return {"error":"no output"}
    try: return json.loads(out)
    except: return {"raw": out}

def parse_iperf_throughput(iperf_json):
    if not iperf_json: return None
    try:
        end = iperf_json.get('end', {})
        for k in ('sum_received','sum_sent'):
            s = end.get(k)
            if s and 'bits_per_second' in s:
                return round(float(s['bits_per_second'])/1e6,3)
        if 'intervals' in end and isinstance(end['intervals'], list):
            try:
                last = end['intervals'][-1]
                if 'sum' in last and 'bits_per_second' in last['sum']:
                    return round(last['sum']['bits_per_second']/1e6,3)
            except: pass
        return None
    except: return None

def percentile_list(arr,p):
    if not arr: return 0.0
    s = sorted(arr); return round(percentile(s,p),3)

def compute_aggregates_for_location(loc):
    items = [x for x in state['logs'] if x['location']==loc]
    if not items: return {}
    def arr_of(k): return [it.get(k) for it in items if it.get(k) is not None]
    ping_baseline_means = [it.get('ping_stats_baseline',{}).get('mean_ms') for it in items if it.get('ping_stats_baseline',{}).get('mean_ms') is not None]
    ping_up_means = [it.get('ping_stats_upload',{}).get('mean_ms') for it in items if it.get('ping_stats_upload',{}).get('mean_ms') is not None]
    ping_down_means = [it.get('ping_stats_download',{}).get('mean_ms') for it in items if it.get('ping_stats_download',{}).get('mean_ms') is not None]
    download_vals = arr_of('download_mbps'); upload_vals = arr_of('upload_mbps')
    def stats(arr):
        if not arr: return {}
        return {"mean": round(mean(arr),3), "median": round(median(arr),3), "p95": percentile_list(arr,95), "min": round(min(arr),3), "max": round(max(arr),3), "count": len(arr)}
    return {
        "ping_baseline_mean_stats": stats(ping_baseline_means),
        "ping_upload_mean_stats": stats(ping_up_means),
        "ping_download_mean_stats": stats(ping_down_means),
        "download_stats_mbps": stats(download_vals),
        "upload_stats_mbps": stats(upload_vals)
    }

# ---------- Runner (same flow) ----------
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
            state['config'] = {"server": server, "iters_per_loc": iters_per_loc, "duration_total": duration_total, "upload_s": up_dur, "download_s": down_dur, "ping_interval": ping_interval, "baseline_s": baseline_s}
        log(f"Runner started server={server} iters={iters_per_loc} dur={duration_total}s (up={up_dur} dn={down_dur}) baseline={baseline_s}s")
        # emit initial status
        socketio.emit('status', prepare_status_payload())

        for loc_idx, loc in enumerate(LOCATIONS):
            with state_lock:
                if state['stop_requested']: break
                state['current_location_idx'] = loc_idx
            log(f"Starting location {loc} ({loc_idx+1}/{len(LOCATIONS)})")
            socketio.emit('status', prepare_status_payload())
            for it in range(1, iters_per_loc+1):
                with state_lock:
                    if state['stop_requested']: break
                    # wait if paused
                while True:
                    with state_lock:
                        if not state['paused'] or state['stop_requested']:
                            break
                    time.sleep(0.5)
                with state_lock:
                    state['current_iteration'] = it
                timestamp = datetime.utcnow().isoformat() + 'Z'
                rssi = get_rssi()
                log(f"[{loc}][iter {it}] RSSI: {rssi}")

                # baseline
                ping_baseline_results = run_ping_collect(server, duration_s=baseline_s, interval=ping_interval)
                ping_stats_baseline = compute_ping_stats(ping_baseline_results)
                log(f"[{loc}][iter {it}] baseline mean={ping_stats_baseline.get('mean_ms')}ms")

                # upload
                ping_up_results = []
                ping_up_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=up_dur, interval=ping_interval)), args=(ping_up_results,))
                ping_up_thread.start()
                iperf_up = run_iperf3(server, duration=up_dur, reverse=False)
                upload_mbps = parse_iperf_throughput(iperf_up)
                if ping_up_thread.is_alive():
                    ping_up_thread.join(timeout=3)

                # download
                ping_down_results = []
                ping_down_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=down_dur, interval=ping_interval)), args=(ping_down_results,))
                ping_down_thread.start()
                iperf_down = run_iperf3(server, duration=down_dur, reverse=True)
                download_mbps = parse_iperf_throughput(iperf_down)
                if ping_down_thread.is_alive():
                    ping_down_thread.join(timeout=3)

                ping_stats_up = compute_ping_stats(ping_up_results)
                ping_stats_down = compute_ping_stats(ping_down_results)
                log(f"[{loc}][iter {it}] up={upload_mbps} Mbps dn={download_mbps} Mbps; ping up mean={ping_stats_up.get('mean_ms')}ms down mean={ping_stats_down.get('mean_ms')}ms")

                entry = {
                    "timestamp": timestamp, "location": loc, "iteration": it, "rssi": rssi,
                    "baseline_seconds": baseline_s, "upload_seconds": up_dur, "download_seconds": down_dur,
                    "ping_samples_baseline_ms": ping_baseline_results, "ping_stats_baseline": ping_stats_baseline,
                    "ping_samples_upload_ms": ping_up_results, "ping_stats_upload": ping_stats_up,
                    "ping_samples_download_ms": ping_down_results, "ping_stats_download": ping_stats_down,
                    "upload_mbps": upload_mbps, "download_mbps": download_mbps,
                    "iperf_upload_raw": iperf_up, "iperf_download_raw": iperf_down
                }
                with state_lock:
                    state['logs'].append(entry)
                # update summary for this location
                with state_lock:
                    state['summary'][loc] = compute_aggregates_for_location(loc)
                # emit updated status to clients
                socketio.emit('status', prepare_status_payload())

            log(f"Finished location {loc}. aggregates updated.")
            socketio.emit('status', prepare_status_payload())
            if not auto_advance:
                with state_lock:
                    state['paused'] = True
                log(f"Paused after {loc}. awaiting resume.")
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
            state['running'] = False; state['paused']=False; state['stop_requested']=False; state['current_location_idx']=0; state['current_iteration']=0
        socketio.emit('status', prepare_status_payload())

def prepare_status_payload():
    # build the same payload as /status endpoint
    with state_lock:
        cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
        current_summary = state['summary'].get(cur_loc) if cur_loc else None
        # history for charts
        history = None
        if cur_loc:
            items = [x for x in state['logs'] if x['location'] == cur_loc]
            if items:
                history = {
                    "iterations": [it.get('iteration') for it in items],
                    "baseline_mean": [it.get('ping_stats_baseline',{}).get('mean_ms') for it in items],
                    "up_mean": [it.get('ping_stats_upload',{}).get('mean_ms') for it in items],
                    "down_mean": [it.get('ping_stats_download',{}).get('mean_ms') for it in items],
                    "upload_mbps": [it.get('upload_mbps') for it in items],
                    "download_mbps": [it.get('download_mbps') for it in items]
                }
        payload = {
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
    return payload

# ---------- Flask routes and socket handlers ----------
@APP.route('/')
def index():
    return render_template_string(INDEX_HTML)

@APP.route('/start', methods=['POST'])
def start():
    with state_lock:
        if state['running']:
            return jsonify({"error":"already running"}), 400
    server = request.form.get('server') or (request.json.get('server') if request.json else None)
    if not server:
        return jsonify({"error":"server required"}), 400
    try: iters = int(request.form.get('iters',5))
    except: iters = 5
    try: duration_total = int(request.form.get('duration',60))
    except: duration_total = 60
    try: baseline_s = int(request.form.get('baseline',8))
    except: baseline_s = 8
    try: ping_interval = float(request.form.get('ping_interval',0.2))
    except: ping_interval = 0.2
    auto = request.form.get('auto') == '1'
    t = threading.Thread(target=runner_thread, args=(server, iters, duration_total, ping_interval, baseline_s, auto), daemon=True)
    t.start()
    # emit status immediately
    socketio.emit('status', prepare_status_payload())
    return jsonify({"message":"started", "server": server, "iters": iters, "duration_total": duration_total, "baseline_s": baseline_s})

@APP.route('/pause', methods=['POST'])
def pause():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['paused'] = True
    socketio.emit('status', prepare_status_payload())
    return jsonify({"message":"paused"})

@APP.route('/resume', methods=['POST'])
def resume():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['paused'] = False
    socketio.emit('status', prepare_status_payload())
    return jsonify({"message":"resumed"})

@APP.route('/stop', methods=['POST'])
def stop():
    with state_lock:
        if not state['running']:
            return jsonify({"error":"not running"}), 400
        state['stop_requested'] = True
        state['paused'] = False
    socketio.emit('status', prepare_status_payload())
    return jsonify({"message":"stop requested"})

@APP.route('/clear_all', methods=['POST'])
def clear_all():
    with state_lock:
        state['logs'] = []
        state['summary'] = {}
    log("Cleared all logs and summaries")
    socketio.emit('status', prepare_status_payload())
    return jsonify({"ok": True})

@APP.route('/clear_location', methods=['POST'])
def clear_location():
    loc = request.form.get('location') or request.args.get('location')
    if not loc: return jsonify({"error":"location required"}), 400
    if loc not in LOCATIONS: return jsonify({"error":"unknown location"}), 400
    with state_lock:
        before = len(state['logs'])
        state['logs'] = [e for e in state['logs'] if e.get('location') != loc]
        if loc in state['summary']: del state['summary'][loc]
        removed = before - len(state['logs'])
    log(f"Cleared {removed} entries for {loc}")
    socketio.emit('status', prepare_status_payload())
    return jsonify({"ok": True, "removed": removed})

@APP.route('/status', methods=['GET'])
def status_http():
    # Keep HTTP status endpoint for tools; same payload as WebSocket
    return jsonify(prepare_status_payload())

# Socket.IO events
@socketio.on('connect')
def handle_connect():
    # send immediate state on connect
    emit('status', prepare_status_payload())

@socketio.on('get_status')
def handle_get_status():
    emit('status', prepare_status_payload())

@socketio.on('get_status_for')
def handle_get_status_for(data):
    # allow client to request status for a given location (not necessary but included)
    loc = data.get('location')
    payload = prepare_status_payload()
    emit('status', payload)

# ---------- Exports ----------
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
                ps_base = it.get('ping_stats_baseline', {}); js_base = ps_base.get('jitter_simple', {})
                ps_up = it.get('ping_stats_upload', {}); js_up = ps_up.get('jitter_simple', {})
                ps_down = it.get('ping_stats_download', {}); js_down = ps_down.get('jitter_simple', {})
                w.writerow({
                    "timestamp": it.get('timestamp'), "location": it.get('location'), "iteration": it.get('iteration'), "rssi": it.get('rssi'),
                    "baseline_seconds": it.get('baseline_seconds'), "ping_baseline_count": ps_base.get('count',0), "ping_baseline_mean_ms": ps_base.get('mean_ms'),
                    "ping_baseline_median_ms": ps_base.get('median_ms'), "ping_baseline_p95_ms": ps_base.get('p95_ms'), "ping_baseline_jitter_rfc3550_ms": ps_base.get('jitter_rfc3550_ms'),
                    "ping_baseline_jitter_simple_mean_ms": js_base.get('mean_ms'),
                    "upload_seconds": it.get('upload_seconds'), "ping_upload_count": ps_up.get('count',0), "ping_upload_mean_ms": ps_up.get('mean_ms'),
                    "ping_upload_median_ms": ps_up.get('median_ms'), "ping_upload_p95_ms": ps_up.get('p95_ms'), "ping_upload_jitter_rfc3550_ms": ps_up.get('jitter_rfc3550_ms'),
                    "ping_upload_jitter_simple_mean_ms": js_up.get('mean_ms'),
                    "download_seconds": it.get('download_seconds'), "ping_download_count": ps_down.get('count',0), "ping_download_mean_ms": ps_down.get('mean_ms'),
                    "ping_download_median_ms": ps_down.get('median_ms'), "ping_download_p95_ms": ps_down.get('p95_ms'), "ping_download_jitter_rfc3550_ms": ps_down.get('jitter_rfc3550_ms'),
                    "ping_download_jitter_simple_mean_ms": js_down.get('mean_ms'),
                    "download_mbps": it.get('download_mbps'), "upload_mbps": it.get('upload_mbps')
                })
    return send_file(fn, as_attachment=True)

# ---------- Run server ----------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Termux network tester — WebSocket (Flask-SocketIO)')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5000, type=int)
    args = parser.parse_args()
    print("Start: python run_tests.py --host 0.0.0.0 --port 5000")
    # Use socketio.run to support WebSocket
    socketio.run(APP, host=args.host, port=args.port)