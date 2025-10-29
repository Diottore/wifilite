#!/usr/bin/env python3
"""
run_tests.py

Flask web app + runner to perform iperf3/ping/RSSI tests on Termux (no root).

Behavior (phase-separated ping + baseline):
- Each iteration is a TOTAL duration (e.g. 60s) split in two phases:
    upload_seconds = duration_total // 2
    download_seconds = duration_total - upload_seconds
- For each iteration:
    1) collect RSSI
    2) run a short baseline ping WITHOUT tráfico (ping_samples_baseline_ms) for baseline_s seconds (default 8s)
    3) run ping concurrently during the UPLOAD phase (ping_samples_upload_ms)
       while iperf3 upload runs for upload_seconds
    4) run ping concurrently during the DOWNLOAD phase (ping_samples_download_ms)
       while iperf3 download (-R) runs for download_seconds
- Ping samples and stats are saved separately for baseline, upload and download phases.
- Jitter is calculated from ping samples using both:
    - simple |RTT_i - RTT_{i-1}| stats (mean/median/p95)
    - RFC3550 estimator (jitter_rfc3550_ms)
- At the end of each location an aggregate summary (mean/median/p95) is computed and shown
  in the web UI under "Resumen de ubicación".
- Exposes a minimal web GUI to control start/pause/resume/stop and to download JSON/CSV.
- Default ping interval is 0.2 s (configurable in UI). Default baseline is 8s.

Requirements (Termux):
- pkg install python iperf3 termux-api
- pip install Flask

Run:
  python run_tests.py --host 0.0.0.0 --port 5000
  Open http://localhost:5000 on the phone or http://<phone-ip>:5000 from another device.
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
<html>
<head><meta charset="utf-8"><title>Termux Network Tester (baseline + phases)</title>
<style>
  body{font-family:Arial;margin:20px}
  .log{white-space:pre-wrap;background:#f8f8f8;padding:10px;border-radius:6px;max-height:300px;overflow:auto}
  .summary { background:#eef; padding:10px; border-radius:6px; margin-top:10px; }
  .small { font-size:0.9em; color:#333; }
  table { border-collapse: collapse; width:100%; margin-top:8px; }
  th, td { border:1px solid #ddd; padding:6px; text-align:left; }
</style>
</head>
<body>
<h2>Termux Network Tester — Baseline + Ping por fase (upload / download)</h2>

<form id="startForm">
Server iperf3: <input name="server" value="192.168.1.1" required>
Iteraciones por ubicación: <select name="iters"><option>3</option><option>4</option><option selected>5</option></select>
Duración TOTAL por iteración (s): <input name="duration" type="number" value="60" min="10">
Baseline (s) antes de cada iteración: <input name="baseline" type="number" value="8" min="2" max="30">
Ping interval (s): <input name="ping_interval" type="number" step="0.05" value="0.2" min="0.05">
Auto-advance: <input type="checkbox" name="auto" value="1">
<button type="submit">Iniciar</button>
</form>

<p class="small">Flujo por iteración: baseline (sin carga) → upload (ping + iperf upload) → download (ping + iperf download).</p>

<button onclick="fetch('/pause',{method:'POST'}).then(()=>refresh())">Pausar</button>
<button onclick="fetch('/resume',{method:'POST'}).then(()=>refresh())">Reanudar</button>
<button onclick="fetch('/stop',{method:'POST'}).then(()=>refresh())">Detener</button>
<button onclick="location.href='/download/json'">Descargar JSON</button>
<button onclick="location.href='/download/csv'">Descargar CSV</button>

<div style="margin-top:12px;">
  <strong>Estado:</strong> <span id="status">idle</span>
  &nbsp;&nbsp; <strong>Último mensaje:</strong> <span id="lastmsg"></span>
</div>

<h3>Resumen de ubicación (última finalizada / actual)</h3>
<div id="summary" class="summary">(sin resumen todavía)</div>

<h3>Logs recientes</h3>
<div class="log" id="logbox">(esperando...)</div>

<script>
document.getElementById('startForm').onsubmit = function(e){
  e.preventDefault();
  fetch('/start', {method:'POST', body: new FormData(e.target)}).then(r=>r.json()).then(j=>{ alert(j.message || JSON.stringify(j)); refresh(); });
};

function buildStatsTable(stats) {
  if(!stats || Object.keys(stats).length===0) return '<div>(no hay datos)</div>';
  return '<table><tr><th>métrica</th><th>valor</th></tr>' +
    '<tr><td>mean</td><td>' + (stats.mean!==undefined ? stats.mean : '-') + '</td></tr>' +
    '<tr><td>median</td><td>' + (stats.median!==undefined ? stats.median : '-') + '</td></tr>' +
    '<tr><td>p95</td><td>' + (stats.p95!==undefined ? stats.p95 : '-') + '</td></tr>' +
    '<tr><td>min</td><td>' + (stats.min!==undefined ? stats.min : '-') + '</td></tr>' +
    '<tr><td>max</td><td>' + (stats.max!==undefined ? stats.max : '-') + '</td></tr>' +
    '<tr><td>count</td><td>' + (stats.count!==undefined ? stats.count : '-') + '</td></tr>' +
    '</table>';
}

function buildSummaryHTML(current_loc, summary) {
  if(!summary) return '<div>(no hay resumen para la ubicación ' + (current_loc||'') + ')</div>';
  var html = '<strong>Ubicación:</strong> ' + (current_loc||'N/A') + '<br/>';
  html += '<div style="margin-top:8px"><strong>Ping (baseline) — estadísticas</strong>' + buildStatsTable(summary.ping_baseline_mean_stats) + '</div>';
  html += '<div style="margin-top:8px"><strong>Ping (upload) — estadísticas</strong>' + buildStatsTable(summary.ping_upload_mean_stats) + '</div>';
  html += '<div style="margin-top:8px"><strong>Ping (download) — estadísticas</strong>' + buildStatsTable(summary.ping_download_mean_stats) + '</div>';
  html += '<div style="margin-top:8px"><strong>Throughput upload —</strong>' + buildStatsTable(summary.upload_stats_mbps) + '</div>';
  html += '<div style="margin-top:8px"><strong>Throughput download —</strong>' + buildStatsTable(summary.download_stats_mbps) + '</div>';
  return html;
}

function refresh(){
  fetch('/status').then(r=>r.json()).then(j=>{
    document.getElementById('status').innerText = j.running ? ((j.current_location || 'N/A') + ' iter ' + j.current_iteration) : 'idle';
    document.getElementById('lastmsg').innerText = j.last_message || '';
    var logs = j.logs || [];
    if(logs.length===0) document.getElementById('logbox').innerText='(no logs)';
    else {
      var txt='';
      for(var i=0;i<Math.min(30,logs.length);i++) txt += JSON.stringify(logs[logs.length-1-i]) + "\\n\\n";
      document.getElementById('logbox').innerText = txt;
    }
    var current_loc = j.current_location;
    var loc_summary = null;
    if (current_loc && j.summary && j.summary[current_loc]) {
      loc_summary = j.summary[current_loc];
    } else {
      if (j.summary) {
        var keys = Object.keys(j.summary);
        if (keys.length>0) {
          var last = keys[keys.length-1];
          loc_summary = j.summary[last];
          current_loc = last;
        }
      }
    }
    document.getElementById('summary').innerHTML = buildSummaryHTML(current_loc, loc_summary);
  }).catch(err=>console.error(err));
}
setInterval(refresh,1500);
refresh();
</script>
</body>
</html>
"""

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
    """Run ping and collect RTT samples in ms. interval in seconds (e.g., 0.2)."""
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
        # split durations (handle odd durations)
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

                # --- BASELINE PHASE (no iperf traffic) ---
                ping_baseline_results = []
                log(f"[{loc}][iter {it}] Running baseline ping for {baseline_s}s (no load)")
                ping_baseline_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=baseline_s, interval=ping_interval)), args=(ping_baseline_results,))
                ping_baseline_thread.start()
                # Wait baseline to finish
                ping_baseline_thread.join(timeout=baseline_s + 3)
                if ping_baseline_thread.is_alive():
                    try:
                        # best effort to stop
                        ping_baseline_thread.join(timeout=1)
                    except:
                        pass
                ping_baseline = ping_baseline_results if isinstance(ping_baseline_results, list) else []
                ping_stats_baseline = compute_ping_stats(ping_baseline)
                log(f"[{loc}][iter {it}] baseline ping count={ping_stats_baseline.get('count',0)} mean={ping_stats_baseline.get('mean_ms')}ms jitter_rfc={ping_stats_baseline.get('jitter_rfc3550_ms')}ms")

                # --- UPLOAD PHASE: ping during upload ---
                ping_up_results = []
                ping_up_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=up_dur, interval=ping_interval)), args=(ping_up_results,))
                ping_up_thread.start()
                log(f"[{loc}][iter {it}] iperf3 upload for {up_dur}s (ping upload running concurrently)")
                iperf_up = run_iperf3(server, duration=up_dur, reverse=False)
                upload_mbps = parse_iperf_throughput(iperf_up)
                log(f"[{loc}][iter {it}] upload Mbps: {upload_mbps}")
                # ensure ping upload finished
                if ping_up_thread.is_alive():
                    ping_up_thread.join(timeout=3)

                # --- DOWNLOAD PHASE: ping during download ---
                ping_down_results = []
                ping_down_thread = threading.Thread(target=lambda lst: lst.extend(run_ping_collect(server, duration_s=down_dur, interval=ping_interval)), args=(ping_down_results,))
                ping_down_thread.start()
                log(f"[{loc}][iter {it}] iperf3 download (-R) for {down_dur}s (ping download running concurrently)")
                iperf_down = run_iperf3(server, duration=down_dur, reverse=True)
                download_mbps = parse_iperf_throughput(iperf_down)
                log(f"[{loc}][iter {it}] download Mbps: {download_mbps}")
                if ping_down_thread.is_alive():
                    ping_down_thread.join(timeout=3)

                # Compute ping stats per phase
                rtts_up = ping_up_results if isinstance(ping_up_results, list) else []
                rtts_down = ping_down_results if isinstance(ping_down_results, list) else []
                ping_stats_up = compute_ping_stats(rtts_up)
                ping_stats_down = compute_ping_stats(rtts_down)
                log(f"[{loc}][iter {it}] ping(up) count={ping_stats_up.get('count',0)} mean={ping_stats_up.get('mean_ms')}ms jitter_rfc={ping_stats_up.get('jitter_rfc3550_ms')}ms")
                log(f"[{loc}][iter {it}] ping(down) count={ping_stats_down.get('count',0)} mean={ping_stats_down.get('mean_ms')}ms jitter_rfc={ping_stats_down.get('jitter_rfc3550_ms')}ms")

                # Save detailed log entry
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

            # compute aggregates for location and expose them to UI
            state['summary'][loc] = compute_aggregates_for_location(loc)
            log(f"Finished location {loc}. aggregates updated.")
            # pause after finishing the location (unless auto)
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
    parser = argparse.ArgumentParser(description='Termux network tester (baseline + ping per phase upload/download) with UI summary')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', default=5000, type=int)
    args = parser.parse_args()
    print("Start: python run_tests.py --host 0.0.0.0 --port 5000")
    APP.run(host=args.host, port=args.port)