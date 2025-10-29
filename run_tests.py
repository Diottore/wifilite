#!/usr/bin/env python3
"""
run_tests.py

Versión con UI mejorada y adaptada a mobile.
Mantiene la funcionalidad: baseline + ping por fase (upload/download), iperf3 TCP,
recolección RSSI (termux-api), logs, export JSON/CSV, pausa/reanudar/stop, resumen por ubicación.
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

APP = Flask(__name__)

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

INDEX_HTML = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Termux Network Tester</title>
<style>
  :root{
    --bg:#f6f8fb;
    --card:#ffffff;
    --muted:#6b7280;
    --accent:#2b6ef6;
    --danger:#e53935;
    --success:#16a34a;
    --glass: rgba(255,255,255,0.6);
    --shadow: 0 6px 20px rgba(16,24,40,0.08);
    font-family: Inter, Roboto, Arial, sans-serif;
  }
  html,body{height:100%;margin:0;background:var(--bg);-webkit-font-smoothing:antialiased}
  .app { max-width:900px; margin:0 auto; padding:14px; }
  header{ display:flex; align-items:center; gap:12px; margin-bottom:12px; }
  .logo { background:linear-gradient(135deg,var(--accent),#6ea8ff); color:white; width:46px; height:46px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-weight:700; box-shadow:var(--shadow); }
  h1{ font-size:1.1rem; margin:0; }
  p.lead{ margin:0; color:var(--muted); font-size:0.9rem; }

  main { display:flex; flex-direction:column; gap:12px; }

  .card { background:var(--card); border-radius:12px; padding:12px; box-shadow:var(--shadow); }
  form .row { display:flex; gap:8px; flex-wrap:wrap; }
  form label{ font-size:0.8rem; color:var(--muted); }
  input[type=text], input[type=number], select { padding:10px 12px; border-radius:8px; border:1px solid #e6e9ef; flex:1; min-width:120px; font-size:0.95rem; }
  .controls { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  button.primary { background:var(--accent); color:white; border:none; padding:10px 14px; border-radius:10px; font-weight:600; font-size:0.95rem; }
  button.ghost { background:transparent; border:1px solid #e6e9ef; padding:10px 12px; border-radius:10px; color:var(--muted); }
  button.danger { background:var(--danger); color:white; border:none; padding:10px 12px; border-radius:10px; }

  .grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  @media (max-width:600px) { .grid { grid-template-columns:1fr; } header h1{ font-size:1rem } }

  .status-row { display:flex; justify-content:space-between; align-items:center; gap:8px; }
  .pill { background:var(--glass); padding:8px 10px; border-radius:999px; font-weight:600; color:#0f172a; display:inline-flex; gap:8px; align-items:center; box-shadow: 0 2px 8px rgba(15,23,42,0.04); }
  .lastmsg { color:var(--muted); font-size:0.85rem; margin-top:6px; }

  .summary-cards { display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }
  .s-card { flex:1 1 48%; background:linear-gradient(180deg, rgba(255,255,255,0.98), var(--card)); padding:10px; border-radius:10px; min-width:140px; }
  .s-card h4{ margin:0 0 6px 0; font-size:0.9rem; }
  .s-val{ font-size:1.1rem; font-weight:700; color:#0f172a; }
  .muted { color:var(--muted); font-size:0.85rem; }

  .logbox { max-height:240px; overflow:auto; white-space:pre-wrap; font-family: monospace; font-size:0.8rem; background:#0f172a0a; padding:10px; border-radius:8px; color:#0f172a; }

  .progress { height:8px; background:#e6eefc; border-radius:999px; overflow:hidden; }
  .progress > i { display:block; height:100%; background:linear-gradient(90deg,var(--accent),#7dd3fc); width:0%; transition:width 600ms linear; }

  footer { text-align:center; color:var(--muted); font-size:0.85rem; margin-top:8px; }

  /* small table for details */
  table.small { width:100%; border-collapse:collapse; margin-top:8px; }
  table.small th, table.small td { text-align:left; padding:6px; border-bottom:1px solid #f1f5f9; font-size:0.9rem; }
</style>
</head>
<body>
<div class="app">
  <header>
    <div class="logo">TN</div>
    <div>
      <h1>Termux Network Tester</h1>
      <p class="lead">Baseline + ping por fase • p1..p8 • mobile-friendly</p>
    </div>
  </header>

  <main>
    <section class="card" id="controlsCard">
      <form id="startForm">
        <div class="row">
          <input type="text" name="server" placeholder="Servidor iperf3 (IP o host)" value="192.168.1.1" required>
          <select name="iters" title="Iteraciones por ubicación">
            <option>3</option><option>4</option><option selected>5</option>
          </select>
        </div>
        <div class="row" style="margin-top:8px;">
          <input type="number" name="duration" min="10" value="60" style="max-width:160px" placeholder="Duración total (s)">
          <input type="number" name="baseline" min="2" max="30" value="8" style="max-width:140px" placeholder="Baseline s">
          <input type="number" step="0.05" name="ping_interval" value="0.2" style="max-width:120px" placeholder="Ping s">
          <label style="display:flex;align-items:center;gap:6px"><input type="checkbox" name="auto" value="1"> Auto</label>
        </div>

        <div class="controls" style="margin-top:10px;">
          <button class="primary" type="submit">▶ Iniciar</button>
          <button class="ghost" type="button" id="pauseBtn">⏸ Pausar</button>
          <button class="ghost" type="button" id="resumeBtn">⏯ Reanudar</button>
          <button class="danger" type="button" id="stopBtn">■ Detener</button>
          <button class="ghost" type="button" id="jsonBtn">⬇ JSON</button>
          <button class="ghost" type="button" id="csvBtn">⬇ CSV</button>
        </div>

        <div class="status-row" style="margin-top:12px;">
          <div class="pill" id="statusPill">idle</div>
          <div style="flex:1;margin-left:10px;">
            <div class="progress" aria-hidden="true"><i id="progressBar"></i></div>
          </div>
        </div>
        <div class="lastmsg" id="lastMsg">(esperando...)</div>
      </form>
    </section>

    <section class="card" id="summaryCard">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <strong>Resumen de ubicación</strong>
        <small class="muted" id="summaryLoc">—</small>
      </div>
      <div class="summary-cards" id="summaryCards">
        <div class="s-card">
          <h4>Ping baseline (mean)</h4>
          <div class="s-val" id="sb_baseline">—</div>
          <div class="muted">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Ping upload (mean)</h4>
          <div class="s-val" id="sb_up">—</div>
          <div class="muted">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Ping download (mean)</h4>
          <div class="s-val" id="sb_down">—</div>
          <div class="muted">mean / median / p95</div>
        </div>
        <div class="s-card">
          <h4>Throughput (Mbps)</h4>
          <div class="s-val" id="sb_thr">—</div>
          <div class="muted">upload / download (mean)</div>
        </div>
      </div>
      <div id="summaryTable" style="margin-top:8px;"></div>
    </section>

    <section class="card">
      <strong>Logs recientes</strong>
      <div class="logbox" id="logbox">(no hay logs)</div>
      <table class="small" id="lastEntries" style="display:none"></table>
    </section>

    <footer class="muted">Hecho para Termux — ejecuta en el teléfono y abre localhost:5000</footer>
  </main>
</div>

<script>
const toFixed = (v, d=3) => (v===undefined || v===null) ? '—' : (Math.round(v*Math.pow(10,d))/Math.pow(10,d));
const el = id => document.getElementById(id);

document.getElementById('startForm').addEventListener('submit', function(e){
  e.preventDefault();
  const form = new FormData(e.target);
  fetch('/start', { method: 'POST', body: form }).then(r=>r.json()).then(j=>{
    if(j.error) alert('Error: ' + j.error); else { updateStatus(); }
  }).catch(err=>alert('Error: ' + err));
});

el('pauseBtn').addEventListener('click', ()=> fetch('/pause',{method:'POST'}).then(()=>updateStatus()));
el('resumeBtn').addEventListener('click', ()=> fetch('/resume',{method:'POST'}).then(()=>updateStatus()));
el('stopBtn').addEventListener('click', ()=> fetch('/stop',{method:'POST'}).then(()=>updateStatus()));
el('jsonBtn').addEventListener('click', ()=> window.location='/download/json');
el('csvBtn').addEventListener('click', ()=> window.location='/download/csv');

function buildSmallTable(summary) {
  if(!summary) return '<div class="muted">Sin resumen todavía</div>';
  const up = summary.ping_upload_mean_stats || {};
  const down = summary.ping_download_mean_stats || {};
  const base = summary.ping_baseline_mean_stats || {};
  const upThr = summary.upload_stats_mbps || {};
  const downThr = summary.download_stats_mbps || {};
  return `<table class="small">
    <tr><th>Métrica</th><th>mean</th><th>median</th><th>p95</th></tr>
    <tr><td>Baseline RTT</td><td>${base.mean||'—'}</td><td>${base.median||'—'}</td><td>${base.p95||'—'}</td></tr>
    <tr><td>Upload RTT</td><td>${up.mean||'—'}</td><td>${up.median||'—'}</td><td>${up.p95||'—'}</td></tr>
    <tr><td>Download RTT</td><td>${down.mean||'—'}</td><td>${down.median||'—'}</td><td>${down.p95||'—'}</td></tr>
    <tr><td>Upload Mbps</td><td>${upThr.mean||'—'}</td><td>${upThr.median||'—'}</td><td>${upThr.p95||'—'}</td></tr>
    <tr><td>Download Mbps</td><td>${downThr.mean||'—'}</td><td>${downThr.median||'—'}</td><td>${downThr.p95||'—'}</td></tr>
  </table>`;
}

function updateProgress(pct){
  el('progressBar').style.width = pct + '%';
}

function updateStatus(){
  fetch('/status').then(r=>r.json()).then(j=>{
    const running = j.running;
    const curLoc = j.current_location || '';
    el('statusPill').innerText = running ? `▶ ${curLoc} · iter ${j.current_iteration}` : 'idle';
    el('lastMsg').innerText = j.last_message || '';
    // logs
    const logs = j.logs || [];
    if(logs.length===0) {
      el('logbox').innerText = '(no hay logs)';
    } else {
      const last = logs.slice(-6).reverse();
      let txt = '';
      last.forEach(it => {
        txt += `[${it.location} i${it.iteration}] up:${it.upload_mbps||'—'} Mbps dn:${it.download_mbps||'—'} rssi:${it.rssi||'—'}\\n`;
        txt += `  baseline mean:${(it.ping_stats_baseline||{}).mean_ms||'—'}ms up mean:${(it.ping_stats_upload||{}).mean_ms||'—'}ms dn mean:${(it.ping_stats_download||{}).mean_ms||'—'}ms\\n\\n`;
      });
      el('logbox').innerText = txt;
    }
    // summary for current or last
    let locSummary = null;
    if (j.summary && curLoc && j.summary[curLoc]) locSummary = j.summary[curLoc];
    else {
      const keys = Object.keys(j.summary || {});
      if(keys.length>0) locSummary = j.summary[keys[keys.length-1]];
    }
    el('summaryLoc').innerText = (curLoc || (locSummary ? Object.keys(j.summary).slice(-1)[0] : '—')) ;
    // set cards
    const base = (locSummary && locSummary.ping_baseline_mean_stats) || {};
    const up = (locSummary && locSummary.ping_upload_mean_stats) || {};
    const down = (locSummary && locSummary.ping_download_mean_stats) || {};
    const upThr = (locSummary && locSummary.upload_stats_mbps) || {};
    const downThr = (locSummary && locSummary.download_stats_mbps) || {};
    el('sb_baseline').innerText = (base.mean!==undefined) ? `${base.mean} / ${base.median} / ${base.p95}` : '—';
    el('sb_up').innerText = (up.mean!==undefined) ? `${up.mean} / ${up.median} / ${up.p95}` : '—';
    el('sb_down').innerText = (down.mean!==undefined) ? `${down.mean} / ${down.median} / ${down.p95}` : '—';
    el('sb_thr').innerText = ((upThr.mean!==undefined) || (downThr.mean!==undefined)) ? `${upThr.mean||'—'} / ${downThr.mean||'—'}` : '—';
    el('summaryTable').innerHTML = buildSmallTable(locSummary);
    // simple progress: if running, show progress based on current_iteration vs total (if config exists)
    if (j.config && j.config.iters_per_loc) {
      const iters = j.config.iters_per_loc;
      const idx = j.current_location ? (['p1','p2','p3','p4','p5','p6','p7','p8'].indexOf(j.current_location) + 1) : 0;
      const totalSteps = 8 * iters;
      const done = ((idx-1) * iters) + (j.current_iteration || 0);
      const pct = Math.min(100, Math.round((done/totalSteps)*100));
      updateProgress(pct);
    }
  }).catch(err=>{
    console.error(err);
  });
}

setInterval(updateStatus, 1500);
updateStatus();
</script>
</body>
</html>
"""

# --- backend logic (same as previous, unchanged) ---

def log(msg):
    ts = datetime.utcnow().isoformat() + 'Z'
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

def run_ping_collect(host, duration_s=60, interval=0.2):
    rtts = []
    cmd = ['ping', '-i', str(interval), '-w', str(int(duration_s)), host]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except Exception:
        count = max(2, int(duration_s / interval))
        cmd = ['ping', '-i', str(interval), '-c', str(count), host]
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
            log(f"Starting location {loc} ({loc_idx+1}/{len(LOCATIONS)})")
            for it in range(1, iters_per_loc+1):
                if state['stop_requested']:
                    break
                while state['paused'] and not state['stop_requested']:
                    log(f"Paused at {loc} iteration {it}...")
                    time.sleep(1)
                state['current_iteration'] = it
                timestamp = datetime.utcnow().isoformat() + 'Z'
                rssi = get_rssi()
                log(f"[{loc}][iter {it}] RSSI: {rssi}")

                # baseline
                ping_baseline_results = []
                log(f"[{loc}][iter {it}] Running baseline ping for {baseline_s}s (no load)")
                ping_baseline_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=baseline_s, interval=ping_interval)), args=(ping_baseline_results,))
                ping_baseline_thread.start()
                ping_baseline_thread.join(timeout=baseline_s + 3)
                if ping_baseline_thread.is_alive():
                    try:
                        ping_baseline_thread.join(timeout=1)
                    except:
                        pass
                ping_baseline = ping_baseline_results if isinstance(ping_baseline_results, list) else []
                ping_stats_baseline = compute_ping_stats(ping_baseline)
                log(f"[{loc}][iter {it}] baseline ping count={ping_stats_baseline.get('count',0)} mean={ping_stats_baseline.get('mean_ms')}ms jitter_rfc={ping_stats_baseline.get('jitter_rfc3550_ms')}ms")

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

                rtts_up = ping_up_results if isinstance(ping_up_results, list) else []
                rtts_down = ping_down_results if isinstance(ping_down_results, list) else []
                ping_stats_up = compute_ping_stats(rtts_up)
                ping_stats_down = compute_ping_stats(rtts_down)
                log(f"[{loc}][iter {it}] ping(up) count={ping_stats_up.get('count',0)} mean={ping_stats_up.get('mean_ms')}ms jitter_rfc={ping_stats_up.get('jitter_rfc3550_ms')}ms")
                log(f"[{loc}][iter {it}] ping(down) count={ping_stats_down.get('count',0)} mean={ping_stats_down.get('mean_ms')}ms jitter_rfc={ping_stats_down.get('jitter_rfc3550_ms')}ms")

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

            state['summary'][loc] = compute_aggregates_for_location(loc)
            log(f"Finished location {loc}. aggregates updated.")
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
    return jsonify({"message":"started", "server": server, "iters": iters, "duration_total": duration_total, "baseline_s": baseline_s, "note": "baseline + phase pings enabled"})

@APP.route('/pause', methods=['POST'])
def pause():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['paused'] = True
    return jsonify({"message":"paused"})

@APP.route('/resume', methods=['POST'])
def resume():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['paused'] = False
    return jsonify({"message":"resumed"})

@APP.route('/stop', methods=['POST'])
def stop():
    if not state['running']:
        return jsonify({"error":"not running"}), 400
    state['stop_requested'] = True
    state['paused'] = False
    return jsonify({"message":"stop requested"})

@APP.route('/status', methods=['GET'])
def status():
    cur_loc = LOCATIONS[state['current_location_idx']] if 0 <= state['current_location_idx'] < len(LOCATIONS) else None
    current_summary = state['summary'].get(cur_loc) if cur_loc else None
    return jsonify({
        "running": state['running'],
        "paused": state['paused'],
        "current_location": cur_loc,
        "current_iteration": state['current_iteration'],
        "last_message": state.get('last_message'),
        "logs": state['logs'][-30:],
        "summary": state['summary'],
        "current_location_summary": current_summary,
        "config": state.get('config', {})
    })

@APP.route('/download/json')
def download_json():
    if not state['logs']:
        return jsonify({"error":"no logs yet"}), 400
    fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
    with open(fn, 'w', encoding='utf-8') as f:
        out = {"generated_at": datetime.utcnow().isoformat()+'Z', "config": state.get('config',{}), "logs": state['logs'], "summary": state.get('summary',{})}
        json.dump(out, f, indent=2, ensure_ascii=False)
    return send_file(fn, as_attachment=True)

@APP.route('/download/csv')
def download_csv():
    if not state['logs']:
        return jsonify({"error":"no logs yet"}), 400
    fn = f"termux_network_test_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
    fieldnames = [
        "timestamp","location","iteration","rssi",
        # baseline ping stats
        "baseline_seconds","ping_baseline_count","ping_baseline_mean_ms","ping_baseline_median_ms","ping_baseline_p95_ms","ping_baseline_jitter_rfc3550_ms","ping_baseline_jitter_simple_mean_ms",
        # upload ping stats
        "upload_seconds","ping_upload_count","ping_upload_mean_ms","ping_upload_median_ms","ping_upload_p95_ms","ping_upload_jitter_rfc3550_ms","ping_upload_jitter_simple_mean_ms",
        # download ping stats
        "download_seconds","ping_download_count","ping_download_mean_ms","ping_download_median_ms","ping_download_p95_ms","ping_download_jitter_rfc3550_ms","ping_download_jitter_simple_mean_ms",
        # throughput
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

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Termux network tester (mobile UI)')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5000, type=int)
    args = parser.parse_args()
    print("Start: python run_tests.py --host 0.0.0.0 --port 5000")
    APP.run(host=args.host, port=args.port)