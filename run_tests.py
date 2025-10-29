#!/usr/bin/env python3
"""
run_tests.py

Versión actualizada: UI más responsive y reactividad en tiempo real usando SSE (Server-Sent Events).
Mantiene baseline + ping por fase (upload/download), iperf3 TCP, RSSI (termux-api), logs,
export JSON/CSV, pausa/reanudar/stop y resumen por ubicación.

Requisitos:
- pkg install python iperf3 termux-api
- pip install Flask

Ejecución:
  python run_tests.py --host 0.0.0.0 --port 5000
Abrir en el teléfono http://localhost:5000 o desde otra máquina http://<ip-telefono>:5000
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

APP = Flask(__name__, static_folder=None)

# Global state
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

LOCATIONS = ["p1","p2","p3","p4","p5","p6","p7","p8"]

# Simple helper to notify updates count (used by SSE loop)
_state_update_counter = 0
_state_update_lock = threading.Lock()

def notify_update():
    global _state_update_counter
    with _state_update_lock:
        _state_update_counter += 1

# HTML template improved responsive + SSE frontend
INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Termux Network Tester — responsive</title>
<link rel="preconnect" href="https://fonts.gstatic.com">
<style>
  :root{
    --bg:#f7f9fc;
    --card:#fff;
    --muted:#6b7280;
    --accent:#2563eb;
    --danger:#ef4444;
    --success:#10b981;
    --radius:12px;
    font-family: Inter, Roboto, Arial, sans-serif;
    font-size: 16px;
  }
  html,body{height:100%;margin:0;background:var(--bg);-webkit-font-smoothing:antialiased}
  .app { max-width:1100px; margin:0 auto; padding:12px; box-sizing:border-box; }
  header { display:flex; gap:12px; align-items:center; margin-bottom:10px; }
  .logo { width:46px; height:46px; background:linear-gradient(135deg,var(--accent),#60a5fa); color:white; border-radius:10px; display:flex; align-items:center; justify-content:center; font-weight:700; box-shadow:0 6px 18px rgba(16,24,40,0.06);}
  h1{ font-size:1.05rem; margin:0; }
  .lead{ color:var(--muted); margin:0; font-size:0.9rem; }

  main { display:flex; flex-direction:column; gap:12px; }

  .card { background:var(--card); border-radius:var(--radius); padding:12px; box-shadow:0 6px 18px rgba(16,24,40,0.06); }
  .controls { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .form-row { display:flex; gap:8px; flex-wrap:wrap; }
  input[type=text], input[type=number], select { padding:10px 12px; border-radius:10px; border:1px solid #e6eef8; font-size:0.95rem; background:#fbfdff; }
  .btn { padding:10px 14px; border-radius:10px; border:none; font-weight:600; font-size:0.95rem; }
  .btn.primary { background:var(--accent); color:white; }
  .btn.ghost { background:transparent; border:1px solid #e6eef8; color:var(--muted); }
  .btn.danger { background:var(--danger); color:white; }

  .status-row { display:flex; align-items:center; gap:12px; margin-top:8px; flex-wrap:wrap; }
  .pill { padding:8px 12px; border-radius:999px; background:rgba(255,255,255,0.7); font-weight:700; color:#0f172a; box-shadow:0 4px 12px rgba(2,6,23,0.04); }

  .grid { display:grid; grid-template-columns: 1fr 1fr; gap:10px; }
  @media (max-width:760px) { .grid { grid-template-columns:1fr; } }
  .summary-cards { display:flex; gap:8px; flex-wrap:wrap; }
  .s-card { flex:1 1 48%; background:linear-gradient(180deg,#fff,#fcfeff); padding:10px; border-radius:10px; min-width:140px; }
  .s-card h4 { margin:0 0 6px 0; font-size:0.9rem; }
  .s-val { font-weight:700; font-size:1.05rem; }

  .charts { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .chart-box { flex:1 1 48%; min-width:140px; background:#fbfdff; border-radius:10px; padding:8px; border:1px solid #eef5ff; }
  .chart-box canvas { width:100% !important; height:96px !important; display:block; }

  .logbox { font-family:monospace; font-size:0.84rem; max-height:220px; overflow:auto; background:linear-gradient(180deg,#fbfdff,#f7fbff); padding:8px; border-radius:8px; color:#0f172a; }

  .small-table { width:100%; border-collapse:collapse; margin-top:8px; }
  .small-table th, .small-table td { padding:6px 8px; border-bottom:1px solid #f1f5f9; text-align:left; font-size:0.9rem; color:#0f172a; }

  footer { text-align:center; color:var(--muted); font-size:0.85rem; margin-top:8px; }

  /* Large touch targets on mobile */
  @media (max-width:420px) {
    .btn { padding:12px 14px; font-size:1rem; border-radius:12px; min-width:48%; }
    .controls { gap:6px; }
  }
</style>

<!-- Chart.js CDN -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">TN</div>
    <div>
      <h1>Termux Network Tester</h1>
      <p class="lead">Responsive • Baseline + ping por fase • p1..p8</p>
    </div>
  </header>

  <main>
    <section class="card" id="controlsCard">
      <form id="startForm">
        <div class="form-row">
          <input type="text" name="server" placeholder="Servidor iperf3 (IP o host)" value="192.168.1.1" required>
          <select name="iters" title="Iteraciones por ubicación">
            <option>3</option><option>4</option><option selected>5</option>
          </select>
        </div>
        <div class="form-row" style="margin-top:8px;">
          <input type="number" name="duration" min="10" value="60" style="max-width:160px" placeholder="Duración total (s)">
          <input type="number" name="baseline" min="2" max="30" value="8" style="max-width:140px" placeholder="Baseline s">
          <input type="number" step="0.05" name="ping_interval" value="0.2" style="max-width:120px" placeholder="Ping s">
          <label style="display:flex;align-items:center;gap:6px"><input type="checkbox" name="auto" value="1"> Auto</label>
        </div>

        <div class="controls" style="margin-top:10px;">
          <button class="btn primary" type="submit">▶ Iniciar</button>
          <button class="btn ghost" type="button" id="pauseBtn">⏸ Pausar</button>
          <button class="btn ghost" type="button" id="resumeBtn">⏯ Reanudar</button>
          <button class="btn danger" type="button" id="stopBtn">■ Detener</button>
          <button class="btn ghost" type="button" id="jsonBtn">⬇ JSON</button>
          <button class="btn ghost" type="button" id="csvBtn">⬇ CSV</button>
        </div>

        <div class="status-row">
          <div class="pill" id="statusPill">idle</div>
          <div style="flex:1;">
            <div style="height:8px;background:#e6eefc;border-radius:999px;overflow:hidden;">
              <div id="progressBar" style="width:0%;height:100%;background:linear-gradient(90deg,var(--accent),#7dd3fc);transition:width 500ms linear"></div>
            </div>
          </div>
        </div>

        <div style="margin-top:8px; color:var(--muted);" id="lastMsg">(esperando...)</div>
      </form>
    </section>

    <section class="card" id="summaryCard">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Resumen de ubicación</strong>
        <small id="summaryLoc" style="color:var(--muted)">—</small>
      </div>

      <div class="summary-cards" id="summaryCards" style="margin-top:8px;">
        <div class="s-card">
          <h4>Ping baseline</h4>
          <div class="s-val" id="sb_baseline">—</div>
          <div style="color:var(--muted)">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Ping upload</h4>
          <div class="s-val" id="sb_up">—</div>
          <div style="color:var(--muted)">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Ping download</h4>
          <div class="s-val" id="sb_down">—</div>
          <div style="color:var(--muted)">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Throughput (Mbps)</h4>
          <div class="s-val" id="sb_thr">—</div>
          <div style="color:var(--muted)">upload / download (mean)</div>
        </div>
      </div>

      <div class="charts">
        <div class="chart-box">
          <div style="color:var(--muted);font-size:0.85rem;margin-bottom:6px">Baseline RTT (ms) por iteración</div>
          <canvas id="chartBaseline"></canvas>
        </div>
        <div class="chart-box">
          <div style="color:var(--muted);font-size:0.85rem;margin-bottom:6px">RTT: upload vs download (ms)</div>
          <canvas id="chartRTT"></canvas>
        </div>
        <div class="chart-box">
          <div style="color:var(--muted);font-size:0.85rem;margin-bottom:6px">Throughput (Mbps)</div>
          <canvas id="chartThroughput"></canvas>
        </div>
        <div class="chart-box">
          <div style="color:var(--muted);font-size:0.85rem;margin-bottom:6px">Últimos logs</div>
          <div id="miniLog" style="font-family:monospace;font-size:0.82rem;color:#0f172a"></div>
        </div>
      </div>

      <div id="summaryTable" style="margin-top:8px;"></div>
    </section>

    <section class="card">
      <strong>Logs recientes</strong>
      <div class="logbox" id="logbox">(no hay logs)</div>
    </section>

    <footer>Hecho para Termux — abre localhost:5000 en el teléfono</footer>
  </main>
</div>

<script>
/* --- Charts setup --- */
let charts = { baseline:null, rtt:null, thr:null };

function createChartBaseline(ctx) {
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets: [{ label:'baseline mean', data: [], borderColor:'#2563eb', backgroundColor:'rgba(37,99,235,0.12)', tension:0.25, pointRadius:0 }]},
    options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{x:{display:false}, y:{ticks:{maxTicksLimit:4}}} }
  });
}
function createChartRTT(ctx) {
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets:[
      { label:'upload', data: [], borderColor:'#10b981', backgroundColor:'rgba(16,185,129,0.06)', tension:0.25, pointRadius:0 },
      { label:'download', data: [], borderColor:'#fb7185', backgroundColor:'rgba(251,113,133,0.06)', tension:0.25, pointRadius:0 }
    ]},
    options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:true,position:'bottom'}}, scales:{x:{display:false}, y:{ticks:{maxTicksLimit:4}}} }
  });
}
function createChartThr(ctx) {
  return new Chart(ctx, {
    type: 'line',
    data: { labels: [], datasets:[
      { label:'upload Mbps', data: [], borderColor:'#7c3aed', backgroundColor:'rgba(124,58,237,0.06)', tension:0.25, pointRadius:0 },
      { label:'download Mbps', data: [], borderColor:'#ea580c', backgroundColor:'rgba(234,88,12,0.06)', tension:0.25, pointRadius:0 }
    ]},
    options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:true,position:'bottom'}}, scales:{x:{display:false}, y:{ticks:{maxTicksLimit:4}}} }
  });
}

function ensureCharts() {
  if(!charts.baseline) charts.baseline = createChartBaseline(document.getElementById('chartBaseline').getContext('2d'));
  if(!charts.rtt) charts.rtt = createChartRTT(document.getElementById('chartRTT').getContext('2d'));
  if(!charts.thr) charts.thr = createChartThr(document.getElementById('chartThroughput').getContext('2d'));
}

/* ResizeObserver to keep charts responsive */
function watchChartsResize(){
  const boxes = document.querySelectorAll('.chart-box');
  const ro = new ResizeObserver(entries=>{
    for(const entry of entries){
      if(charts.baseline) charts.baseline.resize();
      if(charts.rtt) charts.rtt.resize();
      if(charts.thr) charts.thr.resize();
    }
  });
  boxes.forEach(b => ro.observe(b));
}
watchChartsResize();

/* --- SSE: receive server-sent events for quick updates --- */
let es = null;
function startSSE(){
  if(typeof(EventSource) === 'undefined') {
    console.warn('EventSource no soportado; usando polling fallback.');
    setInterval(updateStatus, 1500);
    return;
  }
  if(es) es.close();
  es = new EventSource('/stream');
  es.onmessage = function(evt){
    try {
      const j = JSON.parse(evt.data);
      handleStatusUpdate(j);
    } catch(e){
      console.error('parse sse', e);
    }
  };
  es.onerror = function(e){ console.warn('SSE error, falling back to polling', e); es.close(); setInterval(updateStatus, 1500); };
}

/* Helper UI functions */
const el = id => document.getElementById(id);

function setSummaryCards(locSummary, curLoc){
  if(!locSummary){ el('sb_baseline').innerText='—'; el('sb_up').innerText='—'; el('sb_down').innerText='—'; el('sb_thr').innerText='—'; el('summaryTable').innerHTML=''; return; }
  const base = locSummary.ping_baseline_mean_stats || {};
  const up = locSummary.ping_upload_mean_stats || {};
  const down = locSummary.ping_download_mean_stats || {};
  const upThr = locSummary.upload_stats_mbps || {};
  const downThr = locSummary.download_stats_mbps || {};
  el('sb_baseline').innerText = (base.mean!==undefined) ? `${base.mean} / ${base.median} / ${base.p95}` : '—';
  el('sb_up').innerText = (up.mean!==undefined) ? `${up.mean} / ${up.median} / ${up.p95}` : '—';
  el('sb_down').innerText = (down.mean!==undefined) ? `${down.mean} / ${down.median} / ${down.p95}` : '—';
  el('sb_thr').innerText = ((upThr.mean!==undefined) || (downThr.mean!==undefined)) ? `${upThr.mean||'—'} / ${downThr.mean||'—'}` : '—';
  // small table
  el('summaryTable').innerHTML = buildSmallTable(locSummary);
  el('summaryLoc').innerText = curLoc || '—';
}

function buildSmallTable(summary) {
  if(!summary) return '<div style="color:var(--muted)">Sin resumen todavía</div>';
  const up = summary.ping_upload_mean_stats || {};
  const down = summary.ping_download_mean_stats || {};
  const base = summary.ping_baseline_mean_stats || {};
  const upThr = summary.upload_stats_mbps || {};
  const downThr = summary.download_stats_mbps || {};
  return `<table class="small-table">
    <tr><th>Métrica</th><th>mean</th><th>median</th><th>p95</th></tr>
    <tr><td>Baseline RTT</td><td>${base.mean||'—'}</td><td>${base.median||'—'}</td><td>${base.p95||'—'}</td></tr>
    <tr><td>Upload RTT</td><td>${up.mean||'—'}</td><td>${up.median||'—'}</td><td>${up.p95||'—'}</td></tr>
    <tr><td>Download RTT</td><td>${down.mean||'—'}</td><td>${down.median||'—'}</td><td>${down.p95||'—'}</td></tr>
    <tr><td>Upload Mbps</td><td>${upThr.mean||'—'}</td><td>${upThr.median||'—'}</td><td>${upThr.p95||'—'}</td></tr>
    <tr><td>Download Mbps</td><td>${downThr.mean||'—'}</td><td>${downThr.median||'—'}</td><td>${downThr.p95||'—'}</td></tr>
  </table>`;
}

/* Handle status JSON (from SSE or polling) */
function handleStatusUpdate(j){
  // basic status
  el('statusPill').innerText = j.running ? `▶ ${j.current_location || 'N/A'} · iter ${j.current_iteration}` : 'idle';
  el('lastMsg').innerText = j.last_message || '';
  // recent logs
  const logs = j.logs || [];
  if(logs.length===0) { el('logbox').innerText='(no hay logs)'; el('miniLog').innerText=''; }
  else {
    const last = logs.slice(-6).reverse();
    let txt=''; let mini='';
    last.forEach(it => {
      txt += `[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n`;
      txt += `  base:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`;
      mini += `[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} dn:${it.download_mbps||'—'}\\n`;
    });
    el('logbox').innerText = txt;
    el('miniLog').innerText = mini;
  }
  // summary and charts
  let locSummary = null;
  if (j.summary && j.current_location && j.summary[j.current_location]) locSummary = j.summary[j.current_location];
  else {
    const keys = Object.keys(j.summary || {});
    if(keys.length>0) locSummary = j.summary[keys[keys.length-1]];
  }
  setSummaryCards(locSummary, j.current_location);
  // history for charts
  if(j.current_location_history){
    ensureCharts();
    const h = j.current_location_history;
    const labels = h.iterations.map(n => `#${n}`);
    charts.baseline.data.labels = labels;
    charts.baseline.data.datasets[0].data = h.baseline_mean.map(v => v===null?null:v);
    charts.baseline.update();
    charts.rtt.data.labels = labels;
    charts.rtt.data.datasets[0].data = h.up_mean.map(v=>v===null?null:v);
    charts.rtt.data.datasets[1].data = h.down_mean.map(v=>v===null?null:v);
    charts.rtt.update();
    charts.thr.data.labels = labels;
    charts.thr.data.datasets[0].data = h.upload_mbps.map(v=>v===null?null:v);
    charts.thr.data.datasets[1].data = h.download_mbps.map(v=>v===null?null:v);
    charts.thr.update();
  }
  // progress bar: compute percent by config if available
  if (j.config && j.config.iters_per_loc){
    const iters = j.config.iters_per_loc;
    const idx = j.current_location ? (['p1','p2','p3','p4','p5','p6','p7','p8'].indexOf(j.current_location) + 1) : 0;
    const totalSteps = 8 * iters;
    const done = ((idx-1) * iters) + (j.current_iteration || 0);
    const pct = Math.min(100, Math.round((done/totalSteps)*100));
    document.getElementById('progressBar').style.width = pct + '%';
  }
}

/* --- Controls wiring --- */
document.getElementById('startForm').addEventListener('submit', function(e){
  e.preventDefault();
  const form = new FormData(e.target);
  fetch('/start', { method:'POST', body:form }).then(r=>r.json()).then(j=>{
    if(j.error) alert('Error: '+j.error);
  }).catch(err=>alert('Error: '+err));
});

document.getElementById('pauseBtn').addEventListener('click', ()=> fetch('/pause',{method:'POST'}));
document.getElementById('resumeBtn').addEventListener('click', ()=> fetch('/resume',{method:'POST'}));
document.getElementById('stopBtn').addEventListener('click', ()=> fetch('/stop',{method:'POST'}));
document.getElementById('jsonBtn').addEventListener('click', ()=> window.location='/download/json');
document.getElementById('csvBtn').addEventListener('click', ()=> window.location='/download/csv');

/* Start SSE and default UI init */
startSSE();
ensureCharts();
updateStatus(); // fallback initial poll

/* polling fallback for initial state in case SSE takes a moment */
function updateStatus(){
  fetch('/status').then(r=>r.json()).then(j=>handleStatusUpdate(j)).catch(()=>{});
}
</script>
</body>
</html>
"""

# -------------------- backend measurement logic (unchanged) --------------------
# We'll reuse the previous implementations for ping/iperf/rssi and logs.
# For brevity I include the same helper functions used earlier (safe_call, run_ping_collect, compute_ping_stats, run_iperf3, etc.)
# and the runner_thread that appends to state['logs'] and updates state['summary'].
# At key points we call notify_update() so SSE clients get timely updates.

def log(msg):
    ts = datetime.utcnow().isoformat() + 'Z'
    state['last_message'] = f"{ts} {msg}"
    print(state['last_message'], flush=True)
    notify_update()

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
    """Run ping and collect RTT samples in ms. interval in seconds (e.g., 0.2)."""
    rtts = []
    # prefer IPv4 (avoid unexpected IPv6 routes)
    cmd = ['ping', '-4', '-i', str(interval), '-w', str(int(duration_s)), host]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except Exception:
        # fallback: count-based
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

# Runner thread: baseline + upload + download per iteration; calls notify_update() periodically
def runner_thread(server, iters_per_loc, duration_total, ping_interval=0.2, baseline_s=8, auto_advance=False):
    try:
        duration_total = int(duration_total)
        baseline_s = max(2, int(baseline_s))
        up_dur = duration_total // 2
        down_dur = duration_total - up_dur

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
            state['current_location_idx'] = loc_idx
            notify_update()
            log(f"Starting location {loc} ({loc_idx+1}/{len(LOCATIONS)})")
            for it in range(1, iters_per_loc+1):
                if state['stop_requested']:
                    break
                while state['paused'] and not state['stop_requested']:
                    log(f"Paused at {loc} iteration {it}...")
                    time.sleep(1)
                state['current_iteration'] = it
                notify_update()
                timestamp = datetime.utcnow().isoformat() + 'Z'
                rssi = get_rssi()
                log(f"[{loc}][iter {it}] RSSI: {rssi}")

                # baseline
                ping_baseline_results = []
                log(f"[{loc}][iter {it}] Running baseline ping for {baseline_s}s (no load)")
                ping_baseline_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=baseline_s, interval=ping_interval)), args=(ping_baseline_results,))
                ping_baseline_thread.start()
                ping_baseline_thread.join(timeout=baseline_s + 3)
                ping_baseline = ping_baseline_results if isinstance(ping_baseline_results, list) else []
                ping_stats_baseline = compute_ping_stats(ping_baseline)
                log(f"[{loc}][iter {it}] baseline ping mean={ping_stats_baseline.get('mean_ms')}ms")
                notify_update()

                # upload phase
                ping_up_results = []
                ping_up_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=up_dur, interval=ping_interval)), args=(ping_up_results,))
                ping_up_thread.start()
                log(f"[{loc}][iter {it}] iperf3 upload for {up_dur}s (ping upload running concurrently)")
                iperf_up = run_iperf3(server, duration=up_dur, reverse=False)
                upload_mbps = parse_iperf_throughput(iperf_up)
                log(f"[{loc}][iter {it}] upload Mbps: {upload_mbps}")
                if ping_up_thread.is_alive():
                    ping_up_thread.join(timeout=3)
                notify_update()

                # download phase
                ping_down_results = []
                ping_down_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=down_dur, interval=ping_interval)), args=(ping_down_results,))
                ping_down_thread.start()
                log(f"[{loc}][iter {it}] iperf3 download (-R) for {down_dur}s (ping download running concurrently)")
                iperf_down = run_iperf3(server, duration=down_dur, reverse=True)
                download_mbps = parse_iperf_throughput(iperf_down)
                log(f"[{loc}][iter {it}] download Mbps: {download_mbps}")
                if ping_down_thread.is_alive():
                    ping_down_thread.join(timeout=3)
                notify_update()

                # compute stats
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
                state['logs'].append(entry)
                notify_update()

            # compute aggregates for location and expose them to UI
            state['summary'][loc] = compute_aggregates_for_location(loc)
            log(f"Finished location {loc}. aggregates updated.")
            notify_update()

            if not auto_advance:
                state['paused'] = True
                log(f"Paused after {loc}. Press Reanudar to continue.")
                while state['paused'] and not state['stop_requested']:
                    time.sleep(1)

        log("Runner finished.")
    except Exception as e:
        log(f"Runner exception: {e}")
    finally:
        state['running'] = False
        state['paused'] = False
        state['stop_requested'] = False
        state['current_location_idx'] = 0
        state['current_iteration'] = 0
        notify_update()

# Flask routes

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
    return jsonify({"message":"started", "server": server, "iters": iters, "duration_total": duration_total, "baseline_s": baseline_s})

@APP.route('/pause', methods=['POST'])
def pause():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['paused'] = True
    notify_update()
    return jsonify({"message":"paused"})

@APP.route('/resume', methods=['POST'])
def resume():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['paused'] = False
    notify_update()
    return jsonify({"message":"resumed"})

@APP.route('/stop', methods=['POST'])
def stop():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['stop_requested'] = True
    state['paused'] = False
    notify_update()
    return jsonify({"message":"stop requested"})

@APP.route('/status', methods=['GET'])
def status():
    cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
    current_summary = state['summary'].get(cur_loc) if cur_loc else None
    # Build history arrays for current location (for charts)
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