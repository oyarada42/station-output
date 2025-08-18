from flask import Flask, request, render_template_string, redirect, make_response, jsonify, url_for, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, date, time as dtime
from zoneinfo import ZoneInfo
from jinja2 import DictLoader
import csv, io, os, re, base64

# =========================
#  CONFIG / ENV SWITCHES
# =========================
TZ = ZoneInfo("America/Chicago")
SECRET = os.environ.get("SECRET_KEY", "change-me")
COOKIE_MAX_AGE = 14 * 3600

# Optional Basic Auth (enable by setting BASIC_USER and BASIC_PASS in env)
BASIC_USER = os.environ.get("BASIC_USER")
BASIC_PASS = os.environ.get("BASIC_PASS")
BASIC_AUTH_ENABLED = bool(BASIC_USER and BASIC_PASS)

SHIFT_START_HOUR = 5   # 5 AM
SHIFT_END_HOUR   = 19  # 7 PM

STATIONS = [
    "BTEn-1","BTEn-2","BTEn-3","BTEn-4","BTEn-5","BTEn-6",
    "BTEn-7","BTEn-8","BTEn-9","BTEn-10","BTEn-11","BTEn-12"
]
ROLES    = ["Verifier 1","Verifier 2","Shipper"]
REASONS  = ["Bathroom","Break","System Slow"]

# =========================
#  APP / DB
# =========================
app = Flask(__name__)
app.secret_key = SECRET

# Cookie/session hardening
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Prefer DATABASE_URL; else persist SQLite on /data (Render Disk) to survive restarts
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://", 1)

# Four leading slashes = absolute path; use /data if mounted (Render)
default_sqlite = "sqlite:////data/orders.db" if os.path.isdir("/data") else "sqlite:///orders.db"
app.config["SQLALCHEMY_DATABASE_URI"] = db_url or default_sqlite
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class Event(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    station = db.Column(db.String(40),  nullable=False, index=True)
    role    = db.Column(db.String(40),  nullable=False, index=True)
    stamp   = db.Column(db.String(8),   nullable=False, index=True)
    kind    = db.Column(db.String(16),  nullable=False, index=True)   # order | reject | muda
    ts_utc  = db.Column(db.DateTime,    nullable=False, index=True)

class ReasonEvent(db.Model):
    id      = db.Column(db.Integer, primary_key=True)
    station = db.Column(db.String(40),  nullable=False, index=True)
    role    = db.Column(db.String(40),  nullable=False, index=True)
    stamp   = db.Column(db.String(8),   nullable=False, index=True)
    reason  = db.Column(db.String(32),  nullable=False, index=True)   # Bathroom | Break | System Slow
    ts_utc  = db.Column(db.DateTime,    nullable=False, index=True)

with app.app_context():
    db.create_all()

# =========================
#  OPTIONAL BASIC AUTH
# =========================
def _unauthorized():
    resp = Response("Authentication required", 401)
    resp.headers["WWW-Authenticate"] = 'Basic realm="Restricted"'
    return resp

@app.before_request
def _maybe_require_basic_auth():
    if not BASIC_AUTH_ENABLED:
        return
    # Always allow health check without auth
    if request.path == "/healthz":
        return
    auth = request.authorization
    if auth and auth.username == BASIC_USER and auth.password == BASIC_PASS:
        return
    # Support Authorization: Basic <base64> header even if request.authorization is None
    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header.split(" ",1)[1]).decode("utf-8")
            user, pwd = decoded.split(":",1)
            if user == BASIC_USER and pwd == BASIC_PASS:
                return
        except Exception:
            pass
    return _unauthorized()

# Security headers (cheap, strong defaults)
@app.after_request
def security_headers(resp):
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # Allow inline styles (needed by templates) and inline scripts (your banner + onclick print)
    resp.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline';"
    )
    return resp

# Health check (Render pings this sometimes)
@app.route("/healthz")
def healthz():
    return "ok", 200

# =========================
#  TEMPLATES
# =========================
BASE = """
<!doctype html><html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title or "Station Output" }}</title>
<style>
  :root { --pad:18px; --radius:14px; }
  body { font-family: system-ui,-apple-system,Segoe UI,Arial,sans-serif; margin:16px; color:#0f172a; }
  .wrap { max-width: 1100px; margin: 0 auto; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }
  .btn { padding: 12px 18px; border:0; border-radius: 14px; cursor:pointer; font-size: 16px;
         font-weight: 700; box-shadow: 0 6px 14px rgba(2,6,23,.12); }
  .btn-primary { background:#2563eb; color:#fff; }
  .btn-danger  { background:#ef4444; color:#fff; }
  .btn-ghost   { background:#eef2f7; color:#0f172a; font-weight:700; }
  .btn:active { transform: translateY(1px); box-shadow: 0 4px 10px rgba(2,6,23,.14); }
  .card { border:1px solid #e5e7eb; border-radius: 14px;
          padding: var(--pad); margin: 12px 0; box-shadow: 0 10px 20px rgba(2,6,23,.06); }
  .center { text-align:center; }
  h1,h2,h3 { margin: 8px 0; }
  table { width:100%; border-collapse: separate; border-spacing:0; overflow:hidden;
          border:1px solid #e5e7eb; border-radius: 12px; }
  th, td { padding:11px 12px; text-align:left; }
  th { background:#f8fafc; font-weight:700; border-bottom:1px solid #e5e7eb; position:sticky; top:0; z-index:1; }
  tr:nth-child(even) td { background:#fcfdff; }
  tr:hover td { background:#f1f5f9; }
  td + td, th + th { border-left:1px solid #eef2f7; }
  .big { font-size: 28px; }
  select, input[type=text] { font-size: 18px; padding: 12px; width:100%; box-sizing:border-box; border-radius: 10px; border:1px solid #cbd5e1; }
  .muted { color:#6b7280; }
  .pill { display:inline-block; padding:6px 12px; border-radius:999px; background:#eef2ff; border:1px solid #e5e7eb; }
  .reasons { display:flex; gap:10px; flex-wrap:wrap; justify-content:center; margin-top:14px; }
  .tag { padding:10px 14px; border-radius:12px; border:1px solid #e5e7eb; cursor:pointer; font-weight:700;
         box-shadow: 0 6px 14px rgba(2,6,23,.08); }
  .tag[data-reason="Bathroom"]   { background:#ffedd5; border-color:#fdba74; color:#9a3412; }
  .tag[data-reason="Break"]      { background:#dcfce7; border-color:#86efac; color:#065f46; }
  .tag[data-reason="System Slow"]{ background:#e0e7ff; border-color:#a5b4fc; color:#3730a3; }
  .nz { font-weight:800; color:#111827; }

  /* Station details hour header row */
  .hour-sep td {
    background:#eef2ff;
    border-top:2px solid #c7d2fe;
    font-weight:800;
  }
  .hour-sep .hour-cell { width: 190px; }

  /* Motivational quote bar (horizontal, with typing effect) */
  .moto {
    margin: 6px 0 10px 0;
    width: 100%;
    min-height: 38px;
    display:flex; align-items:center;
    justify-content:center;
    border-radius: 12px;
    padding: 8px 14px;
    background: linear-gradient(90deg, #e0f2fe, #e9d5ff);
    box-shadow: 0 6px 14px rgba(2,6,23,.08);
    font-weight: 800;
    letter-spacing: .5px;
  }
  .moto .text { font-size: 15px; white-space: nowrap; overflow: hidden; }
  .moto .cursor { display:inline-block; width:1ch; animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  .footer { margin-top: 16px; text-align:center; font-size:12px; color:#6b7280; }
  @media print { .footer { position: fixed; bottom: 10px; left:0; right:0; color:#4b5563; } }
  @media print {
    body { margin:0; }
    .row, .moto { display:none; }
    .card { box-shadow:none; border:1px solid #d1d5db; page-break-inside: avoid; }
    table { page-break-inside: auto; }
    tr { page-break-inside: avoid; page-break-after:auto; }
  }
</style>
</head><body>
<div class="wrap">
  <div class="row no-print">
    <a class="btn btn-ghost" href="{{ url_for('home') }}">Home</a>
    <a class="btn btn-ghost" href="{{ url_for('dashboard') }}">Dashboard</a>
    <a class="btn btn-ghost" href="{{ url_for('export_today_csv') }}">Export Today (CSV)</a>
    <a class="btn btn-ghost" href="{{ url_for('export_station_totals_csv') }}">Export Station Totals (CSV)</a>
    <button class="btn btn-primary" onclick="window.print()">Print</button>
  </div>

  <!-- Horizontal motivational bar -->
  <div class="moto">
    <span id="motoText" class="text"></span><span class="cursor">|</span>
  </div>

  {% block content %}{% endblock %}
  <footer class="footer">{{ copyright }}</footer>
</div>

<script>
  // --- Motivational quotes (cycles every 30 minutes) ---
  const QUOTES = [
    "THANK YOU FOR YOUR HARD WORK AND DEDICATION. YOU MAKE A DIFFERENCE.",
    "KEEP UP THE GOOD WORK!!!"
  ];
  const TYPE_SPEED = 45;                 // ms per character
  const ROTATE_EVERY = 30 * 60 * 1000;   // 30 minutes

  const el = document.getElementById("motoText");
  let idx = 0;

  function typeQuote(txt, cb){
    el.textContent = "";
    let i = 0;
    const timer = setInterval(() => {
      el.textContent += txt[i++];
      if (i >= txt.length) { clearInterval(timer); if (cb) cb(); }
    }, TYPE_SPEED);
  }

  function rotate(){
    const q = QUOTES[idx % QUOTES.length];
    typeQuote(q);
    idx++;
    setTimeout(rotate, ROTATE_EVERY);
  }
  rotate();
</script>
</body></html>
"""

HOME = """
{% extends "base.html" %}
{% block content %}
  {% if not station or not role or not stamp %}
    <h2>Start Counting</h2>
    <div class="card">
      <form method="post" action="{{ url_for('start') }}" id="startForm">
        <div class="row">
          <div style="flex:1; min-width:260px;">
            <label><b>Select station</b></label>
            <select name="station" required>
              <option value="" disabled {{ 'selected' if not station }}>Select station</option>
              {% for s in stations %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
            </select>
          </div>
          <div style="flex:1; min-width:260px;">
            <label><b>Select role</b></label>
            <select name="role" required>
              <option value="" disabled {{ 'selected' if not role }}>Select role</option>
              {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
            </select>
          </div>
        </div>
        <div style="margin-top:12px;">
          <label><b>Enter your 4-digit Stamp #</b></label>
          <input id="stampInput" name="stamp" type="text" inputmode="numeric" pattern="\\d{4}" maxlength="4" placeholder="####" required>
        </div>
        <div style="margin-top:14px;">
          <button class="btn btn-primary" type="submit">Continue</button>
          <p class="muted">We’ll remember Station + Role + Stamp on this device for 14 hours.</p>
        </div>
      </form>
    </div>
    <script>
      const inp = document.getElementById('stampInput');
      inp.addEventListener('input', ()=>{ inp.value = inp.value.replace(/\\D/g,'').slice(0,4); });
      document.getElementById('startForm').addEventListener('submit', (e)=>{
        if(!/^\\d{4}$/.test(inp.value)) { alert('Stamp must be 4 digits'); e.preventDefault(); }
      });
    </script>
  {% else %}
    <h1 class="center">Daily Station Output</h1>
    <p class="center">
      <span class="pill">{{ station }}</span> ·
      <span class="pill">{{ role }}</span> ·
      <span class="pill">Stamp {{ stamp }}</span>
    </p>

    <div class="card center">
      <form id="orderForm" method="post" action="{{ url_for('tap_order') }}" style="display:inline-block;">
        <button class="btn btn-primary big" type="submit">+1 ORDER</button>
      </form>
      {% if 'Shipper' in role %}
        <form id="excForm" method="post" action="{{ url_for('tap_muda') }}" style="display:inline-block; margin-left:10px;">
          <button class="btn btn-danger big" type="submit">Muda</button>
        </form>
      {% else %}
        <form id="excForm" method="post" action="{{ url_for('tap_reject') }}" style="display:inline-block; margin-left:10px;">
          <button class="btn btn-danger big" type="submit">Reject</button>
        </form>
      {% endif %}

      <div class="reasons">
        {% for r in reasons %}
          {# keep value "System Slow" but label "System Slow/Other" #}
          <button class="tag" data-reason="{{ r }}">{{ 'System Slow/Other' if r == 'System Slow' else r }}</button>
        {% endfor %}
      </div>

      <form id="reasonForm" method="post" action="{{ url_for('tap_reason') }}" style="display:none;">
        <input type="hidden" name="reason" id="reasonInput">
      </form>

      <p id="msg" style="min-height:1.4em;"></p>
      <div><p><b>This hour (orders):</b> <span id="hourCount">{{ hour_order_count }}</span></p></div>
    </div>

    <div class="card">
      <h3>Today ({{ shift_label }}) — {{ station }} / {{ role }} / Stamp {{ stamp }}</h3>
      <table id="todayTable">
        <tr>
          <th>Hour</th>
          <th>Orders</th>
          <th>{{ ex_label }}</th>
          <th>Bathroom</th>
          <th>Break</th>
          <th>System Slow/Other</th>
        </tr>
        {% for row in today_rows %}
          <tr>
            <td>{{ row[0] }}</td>
            <td>{{ row[1] }}</td>
            <td>{{ row[2] }}</td>
            <td>{{ row[3] }}</td>
            <td>{{ row[4] }}</td>
            <td>{{ row[5] }}</td>
          </tr>
        {% endfor %}
      </table>
    </div>

    <p class="muted center">Changing station/role/stamp? <a href="{{ url_for('switch') }}">Switch selection</a></p>

    <script>
      function boldNonZeroCells() {
        const t = document.getElementById('todayTable');
        if (!t) return;
        [...t.querySelectorAll('tr')].forEach((tr,i)=>{
          if(i===0) return;
          [...tr.children].forEach((td,idx)=>{
            if(idx===0) return;
            const n = Number(td.textContent.trim());
            if(!Number.isNaN(n) && n>0) td.classList.add('nz'); else td.classList.remove('nz');
          });
        });
      }
      function renderRows(rows){
        const t = document.getElementById('todayTable');
        t.innerHTML = `
          <tr>
            <th>Hour</th><th>Orders</th><th>{{ ex_label }}</th>
            <th>Bathroom</th><th>Break</th><th>System Slow/Other</th>
          </tr>`;
        rows.forEach(r=>{
          const tr = document.createElement('tr');
          tr.innerHTML = `<td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td><td>${r[5]}</td>`;
          t.appendChild(tr);
        });
        boldNonZeroCells();
      }
      async function call(action, formId=null){
        let opts = { method:'POST' };
        if(formId){ opts.body = new FormData(document.getElementById(formId)); }
        const res = await fetch(action, opts);
        if(!res.ok) return;
        const data = await res.json();
        document.getElementById('msg').textContent = data.message || 'Logged!';
        if (data.hour_order_count !== undefined) document.getElementById('hourCount').textContent = data.hour_order_count;
        if (data.today_rows) renderRows(data.today_rows);
        setTimeout(()=>{ document.getElementById('msg').textContent=''; }, 900);
      }
      document.getElementById('orderForm').addEventListener('submit', (e)=>{ e.preventDefault(); call('{{ url_for('tap_order') }}'); });
      document.getElementById('excForm').addEventListener('submit', (e)=>{ e.preventDefault(); call(document.getElementById('excForm').action); });
      document.querySelectorAll('.tag').forEach(b=>{
        b.addEventListener('click', ()=>{
          document.getElementById('reasonInput').value = b.dataset.reason;
          call('{{ url_for('tap_reason') }}', 'reasonForm');
        });
      });
      boldNonZeroCells();
    </script>
  {% endif %}
{% endblock %}
"""

DASH = """
{% extends "base.html" %}
{% block content %}
  <h2 class="print-header">Management Dashboard — Today ({{ shift_label }})</h2>

  <div class="card">
    <h3>Station Totals Today (Shipper only)</h3>
    <table>
      <tr>
        <th>Station</th>
        <th>Shipped Today</th>
      </tr>
      {% for station, shipped_today in station_totals %}
        <tr>
          <td>{{ station }}</td>
          <td class="{{ 'nz' if shipped_today>0 else '' }}">{{ shipped_today }}</td>
        </tr>
      {% endfor %}
    </table>
  </div>

  <div class="card">
    <div class="station-head">
      <h3>Station details</h3>
      <form method="get" action="{{ url_for('dashboard') }}" class="row" style="margin:0;">
        <select name="station" required>
          <option value="">Select station</option>
          {% for s in stations %}
            <option value="{{ s }}" {{ 'selected' if s == selected_station else '' }}>{{ s }}</option>
          {% endfor %}
        </select>

        {% if selected_station %}
          <a class="btn btn-ghost" href="{{ url_for('dashboard') }}">Hide</a>
          <button class="btn btn-primary" type="button" onclick="window.print()">Print Station</button>
        {% else %}
          <button class="btn btn-primary" type="submit">Show</button>
        {% endif %}
      </form>
    </div>

    {% if selected_station %}
      <h3>Station details — {{ selected_station }}</h3>
      <table>
        <tr>
          <th style="width:190px;">Time</th>
          <th>Stamp #</th><th>Role</th>
          <th>Orders</th><th>Reject/Muda</th>
          <th>Break</th><th>Bathroom</th><th>System Slow/Other</th>
        </tr>

        {% for group in station_details %}
          <tr class="hour-sep">
            <td class="hour-cell">{{ group.hour }}</td>
            <td colspan="7"></td>
          </tr>

          {% if group.rows|length == 0 %}
            <tr><td></td><td colspan="7">—</td></tr>
          {% else %}
            {% for r in group.rows %}
              <tr>
                <td></td>
                <td>{{ r.stamp }}</td>
                <td>{{ r.role }}</td>
                <td class="{{ 'nz' if r.orders>0 else '' }}">{{ r.orders }}</td>
                <td class="{{ 'nz' if r.ex>0 else '' }}">{{ r.ex }}</td>
                <td class="{{ 'nz' if r.brk>0 else '' }}">{{ r.brk }}</td>
                <td class="{{ 'nz' if r.bth>0 else '' }}">{{ r.bth }}</td>
                <td class="{{ 'nz' if r.sys>0 else '' }}">{{ r.sys }}</td>
              </tr>
            {% endfor %}
          {% endif %}
        {% endfor %}
      </table>
    {% else %}
      <p class="muted">Choose a station to see hourly shipped totals and employee breakdown. This section prints cleanly.</p>
    {% endif %}
  </div>
{% endblock %}
"""

app.jinja_loader = DictLoader({"base.html": BASE})

# =========================
#  HELPERS
# =========================
def now_local() -> datetime:
    return datetime.now(tz=TZ)

def fmt_ampm(dt_local: datetime) -> str:
    return dt_local.strftime("%I:%M %p").lstrip("0")

def shift_bounds_local(day: date):
    start = datetime.combine(day, dtime(hour=SHIFT_START_HOUR), tzinfo=TZ)
    end   = datetime.combine(day, dtime(hour=SHIFT_END_HOUR), tzinfo=TZ)
    return start, end

def fixed_hour_labels(day: date):
    start, end = shift_bounds_local(day)
    labels, cur = [], start
    while cur < end:
        nxt = cur + timedelta(hours=1)
        labels.append(f"{fmt_ampm(cur)} – {fmt_ampm(nxt)}")
        cur = nxt
    return labels

def utc_from_local(dt_local: datetime) -> datetime:
    return dt_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

def cookie_get(name): return request.cookies.get(name)
def set_cookie(resp, key, value):
    # secure=True ensures HTTPS-only cookies on Render
    resp.set_cookie(key, value, max_age=COOKIE_MAX_AGE, httponly=True, samesite="Lax", secure=True)

def is_valid_stamp(stamp: str) -> bool: return bool(re.fullmatch(r"\d{4}", stamp or ""))

def today_rows_for(station: str, role: str, stamp: str):
    day = now_local().date()
    shift_start_loc, shift_end_loc = shift_bounds_local(day)
    start_utc = utc_from_local(shift_start_loc)
    end_utc   = utc_from_local(shift_end_loc)

    labels = fixed_hour_labels(day)
    b_orders = [0]*len(labels)
    b_exc    = [0]*len(labels)
    b_bath   = [0]*len(labels)
    b_break  = [0]*len(labels)
    b_sys    = [0]*len(labels)

    evs = (Event.query
             .filter(Event.station==station, Event.role==role, Event.stamp==stamp,
                     Event.ts_utc >= start_utc, Event.ts_utc < end_utc)
             .all())
    for e in evs:
        local_dt = e.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
        if not (shift_start_loc <= local_dt < shift_end_loc): continue
        idx = int((local_dt - shift_start_loc).total_seconds() // 3600)
        if e.kind == 'order': b_orders[idx] += 1
        elif e.kind in ('reject', 'muda'): b_exc[idx] += 1

    rs = (ReasonEvent.query
            .filter(ReasonEvent.station==station, ReasonEvent.role==role, ReasonEvent.stamp==stamp,
                    ReasonEvent.ts_utc >= start_utc, ReasonEvent.ts_utc < end_utc)
            .all())
    for r in rs:
        local_dt = r.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
        if not (shift_start_loc <= local_dt < shift_end_loc): continue
        idx = int((local_dt - shift_start_loc).total_seconds() // 3600)
        if r.reason == "Bathroom": b_bath[idx] += 1
        elif r.reason == "Break":  b_break[idx] += 1
        elif r.reason == "System Slow": b_sys[idx] += 1

    hour_start_loc = now_local().replace(minute=0, second=0, microsecond=0)
    hour_order_count = (Event.query
                          .filter(Event.kind=='order',
                                  Event.station==station,
                                  Event.role==role,
                                  Event.stamp==stamp,
                                  Event.ts_utc >= utc_from_local(hour_start_loc))
                          .count())

    rows = [(labels[i], b_orders[i], b_exc[i], b_bath[i], b_break[i], b_sys[i])
            for i in range(len(labels))]
    return rows, hour_order_count, shift_start_loc, shift_end_loc

# =========================
#  ROUTES
# =========================
@app.route("/", methods=["GET"])
def home():
    station = cookie_get("station")
    role    = cookie_get("role")
    stamp   = cookie_get("stamp")

    if not (station and role and stamp):
        return render_template_string(HOME,
                                      stations=STATIONS, roles=ROLES,
                                      station=station, role=role, stamp=stamp,
                                      title="Station Output")

    rows, hour_order_count, s_loc, e_loc = today_rows_for(station, role, stamp)
    ex_label = "Muda" if "Shipper" in role else "Rejects"

    return render_template_string(HOME,
                                  station=station, role=role, stamp=stamp,
                                  hour_order_count=hour_order_count,
                                  today_rows=rows, reasons=REASONS,
                                  ex_label=ex_label,
                                  shift_label=f"{fmt_ampm(s_loc)}–{fmt_ampm(e_loc)}",
                                  title="Station Output")

@app.route("/start", methods=["POST"])
def start():
    station = (request.form.get("station") or "").strip()
    role    = (request.form.get("role")    or "").strip()
    stamp   = (request.form.get("stamp")   or "").strip()
    if not station or not role or not is_valid_stamp(stamp):
        return redirect(url_for("home"))
    resp = make_response(redirect(url_for("home")))
    set_cookie(resp, "station", station)
    set_cookie(resp, "role", role)
    set_cookie(resp, "stamp", stamp)
    return resp

@app.route("/switch")
def switch():
    resp = make_response(redirect(url_for("home")))
    for k in ["station", "role", "stamp"]:
        resp.delete_cookie(k)
    return resp

def _sel():
    station = cookie_get("station"); role = cookie_get("role"); stamp = cookie_get("stamp")
    if not (station and role and is_valid_stamp(stamp)): return None
    return station, role, stamp

@app.route("/tap_order", methods=["POST"])
def tap_order():
    sel = _sel()
    if not sel: return jsonify(error="missing-selections"), 400
    station, role, stamp = sel
    db.session.add(Event(station=station, role=role, stamp=stamp, kind='order', ts_utc=datetime.utcnow()))
    db.session.commit()
    rows, hoc, *_ = today_rows_for(station, role, stamp)
    return jsonify(ok=True, hour_order_count=hoc, today_rows=rows, message="+1 order")

@app.route("/tap_reject", methods=["POST"])
def tap_reject():
    sel = _sel()
    if not sel: return jsonify(error="missing-selections"), 400
    station, role, stamp = sel
    db.session.add(Event(station=station, role=role, stamp=stamp, kind='reject', ts_utc=datetime.utcnow()))
    db.session.commit()
    rows, hoc, *_ = today_rows_for(station, role, stamp)
    return jsonify(ok=True, hour_order_count=hoc, today_rows=rows, message="Reject logged")

@app.route("/tap_muda", methods=["POST"])
def tap_muda():
    sel = _sel()
    if not sel: return jsonify(error="missing-selections"), 400
    station, role, stamp = sel
    db.session.add(Event(station=station, role=role, stamp=stamp, kind='muda', ts_utc=datetime.utcnow()))
    db.session.commit()
    rows, hoc, *_ = today_rows_for(station, role, stamp)
    return jsonify(ok=True, hour_order_count=hoc, today_rows=rows, message="Muda logged")

@app.route("/tap_reason", methods=["POST"])
def tap_reason():
    sel = _sel()
    if not sel: return jsonify(error="missing-selections"), 400
    station, role, stamp = sel
    reason = (request.form.get("reason") or "").strip()
    if reason not in REASONS: return jsonify(error="invalid-reason"), 400
    db.session.add(ReasonEvent(station=station, role=role, stamp=stamp, reason=reason, ts_utc=datetime.utcnow()))
    db.session.commit()
    rows, hoc, *_ = today_rows_for(station, role, stamp)
    return jsonify(ok=True, hour_order_count=hoc, today_rows=rows, message=f"{reason} logged")

# =========================
#  DASHBOARD
# =========================
@app.route("/dashboard")
def dashboard():
    day = now_local().date()
    start_loc, end_loc = shift_bounds_local(day)
    start_utc = utc_from_local(start_loc)
    end_utc   = utc_from_local(end_loc)

    evs = (Event.query
             .filter(Event.ts_utc >= start_utc, Event.ts_utc < end_utc)
             .all())

    # Shipped totals by station (shipper orders only)
    station_day = {s:0 for s in STATIONS}
    for e in evs:
        if e.role == "Shipper" and e.kind == "order":
            station_day[e.station] = station_day.get(e.station,0) + 1

    station_totals = sorted([(s, station_day.get(s,0)) for s in STATIONS],
                            key=lambda r: r[1], reverse=True)

    selected_station = request.args.get("station") or None
    station_details = []

    if selected_station:
        labels = fixed_hour_labels(day)
        start_loc, end_loc = shift_bounds_local(day)

        groups = [ {} for _ in labels ]
        def hour_index(dt_utc: datetime) -> int:
            local_dt = dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
            if not (start_loc <= local_dt < end_loc): return -1
            return int((local_dt - start_loc).total_seconds() // 3600)

        rs = (ReasonEvent.query
               .filter(ReasonEvent.ts_utc >= start_utc, ReasonEvent.ts_utc < end_utc)
               .all())

        for e in evs:
            if e.station != selected_station: continue
            idx = hour_index(e.ts_utc)
            if idx < 0: continue
            key = (e.stamp, e.role)
            groups[idx].setdefault(key, {"orders":0,"ex":0,"brk":0,"bth":0,"sys":0})
            if e.kind == "order": groups[idx][key]["orders"] += 1
            elif e.kind in ("reject","muda"): groups[idx][key]["ex"] += 1

        for r in rs:
            if r.station != selected_station: continue
            idx = hour_index(r.ts_utc)
            if idx < 0: continue
            key = (r.stamp, r.role)
            groups[idx].setdefault(key, {"orders":0,"ex":0,"brk":0,"bth":0,"sys":0})
            if   r.reason == "Break":       groups[idx][key]["brk"] += 1
            elif r.reason == "Bathroom":    groups[idx][key]["bth"] += 1
            elif r.reason == "System Slow": groups[idx][key]["sys"] += 1

        role_rank = {"Shipper":0, "Verifier 1":1, "Verifier 2":2}
        for i, ppl in enumerate(groups):
            rows = []
            for (stamp, role), v in sorted(ppl.items(), key=lambda k: (role_rank.get(k[0][1],9), k[0][0])):
                rows.append({
                    "stamp": stamp, "role": role,
                    "orders": v["orders"], "ex": v["ex"],
                    "brk": v["brk"], "bth": v["bth"], "sys": v["sys"]
                })
            station_details.append({"hour": labels[i], "rows": rows})

    shift_label = f"{fmt_ampm(start_loc)}–{fmt_ampm(end_loc)}"
    return render_template_string(
        DASH,
        stations=STATIONS,
        station_totals=station_totals,
        selected_station=selected_station,
        station_details=station_details,
        shift_label=shift_label,
        title="Dashboard"
    )

# =========================
#  CSV EXPORTS
# =========================
@app.route("/export/today.csv")
def export_today_csv():
    day = now_local().date()
    start_loc, end_loc = shift_bounds_local(day)
    start_utc = utc_from_local(start_loc)
    end_utc   = utc_from_local(end_loc)

    evs = (Event.query
             .filter(Event.ts_utc >= start_utc, Event.ts_utc < end_utc)
             .order_by(Event.ts_utc.asc())
             .all())
    rs = (ReasonEvent.query
             .filter(ReasonEvent.ts_utc >= start_utc, ReasonEvent.ts_utc < end_utc)
             .order_by(ReasonEvent.ts_utc.asc())
             .all())

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["timestamp_local","timestamp_utc","station","role","stamp","type","value"])
    for e in evs:
        local_dt = e.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
        w.writerow([local_dt.strftime("%m/%d/%Y %I:%M:%S %p"),
                    e.ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    e.station, e.role, e.stamp,
                    "event", e.kind])
    for r in rs:
        local_dt = r.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
        w.writerow([local_dt.strftime("%m/%d/%Y %I:%M:%S %p"),
                    r.ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    r.station, r.role, r.stamp,
                    "reason", r.reason])
    data = out.getvalue().encode("utf-8")
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=today_station_output.csv"})

@app.route("/export/stations.csv")
def export_station_totals_csv():
    day = now_local().date()
    start_loc, end_loc = shift_bounds_local(day)
    start_utc = utc_from_local(start_loc)
    end_utc   = utc_from_local(end_loc)

    evs = (Event.query
             .filter(Event.ts_utc >= start_utc, Event.ts_utc < end_utc)
             .all())

    station_day = {s:0 for s in STATIONS}
    for e in evs:
        if e.role == "Shipper" and e.kind == "order":
            station_day[e.station] = station_day.get(e.station,0) + 1

    rows = sorted([(s, station_day.get(s,0)) for s in STATIONS],
                  key=lambda r: r[1], reverse=True)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["station","shipped_today"])
    for r in rows: w.writerow(r)
    data = out.getvalue().encode("utf-8")
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=today_station_totals.csv"})

# Footer copyright (will appear on every page)
@app.context_processor
def inject_footer():
    return dict(copyright="© 2025 Abdi. All rights reserved.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
