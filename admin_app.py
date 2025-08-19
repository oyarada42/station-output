# admin_app.py
#
# Password-protected Manager panel under /admin
# Lets managers delete the most-recent tap (order/reject/muda) or most-recent reason
# for a selected Station/Role/Stamp within today's shift window.
#
# Uses a Blueprint so app.py stays almost untouched.

from flask import Blueprint, render_template_string, request, redirect, url_for, session
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

# Import models/helpers already defined in app.py
# (Make sure you register this blueprint near the END of app.py so these symbols exist.)
from app import (
    db, Event, ReasonEvent,
    STATIONS, ROLES,
    now_local, utc_from_local, shift_bounds_local
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

TZ = ZoneInfo("America/Chicago")
ADMIN_PASS = os.environ.get("ADMIN_PASS")  # set on Render -> Environment

# ---------- tiny auth helpers (no change to app.py needed) ----------
def _is_authed() -> bool:
    until = session.get("admin_until")
    if not until: 
        return False
    try:
        return datetime.utcnow() < datetime.fromisoformat(until)
    except Exception:
        return False

def _require_auth():
    if not _is_authed():
        return redirect(url_for("admin.login"))

# store a 2-hour session window (clears on browser close, too)
def _grant_auth():
    session["admin_until"] = (datetime.utcnow() + timedelta(hours=2)).isoformat()

# ---------- templates (inline so this file is self-contained) ----------
LOGIN_HTML = """
<!doctype html>
<title>Manager Login</title>
<style>
  body{font-family:system-ui,Segoe UI,Arial;margin:40px;color:#111}
  .card{max-width:420px;margin:auto;border:1px solid #e5e7eb;border-radius:12px;padding:18px;box-shadow:0 8px 16px rgba(2,6,23,.06)}
  input,button{font-size:16px;padding:10px;width:100%;box-sizing:border-box;border-radius:10px;border:1px solid #cbd5e1}
  button{background:#2563eb;color:#fff;border:0;font-weight:700;margin-top:10px;cursor:pointer}
  .muted{color:#6b7280;margin-top:8px}
  .err{color:#b91c1c;margin:8px 0}
</style>
<div class="card">
  <h2>Manager Login</h2>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  {% if not have_pass %}
    <p class="muted">ADMIN_PASS is not set on the server. Ask IT to add it in Render → Environment.</p>
  {% else %}
  <form method="post">
    <label>Password</label>
    <input name="password" type="password" autofocus required>
    <button type="submit">Sign in</button>
  </form>
  {% endif %}
</div>
"""

PANEL_HTML = """
<!doctype html>
<title>Manager Panel</title>
<style>
  body{font-family:system-ui,Segoe UI,Arial;margin:14px;color:#111}
  .wrap{max-width:1000px;margin:auto}
  .card{border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin:10px 0;box-shadow:0 8px 16px rgba(2,6,23,.06)}
  .row{display:flex;gap:10px;flex-wrap:wrap}
  select,input,button{font-size:16px;padding:10px;border-radius:10px;border:1px solid #cbd5e1}
  button{background:#2563eb;color:#fff;border:0;font-weight:700;cursor:pointer}
  .danger{background:#ef4444}
  .muted{color:#6b7280}
  .msg{margin:8px 0}
  table{width:100%;border-collapse:separate;border-spacing:0;border:1px solid #e5e7eb;border-radius:10px}
  th,td{padding:10px;text-align:left}
  th{background:#f8fafc}
</style>
<div class="wrap">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2>Manager Panel</h2>
      <div>
        <a href="{{ url_for('dashboard') }}" style="margin-right:8px">Dashboard</a>
        <a href="{{ url_for('home') }}" style="margin-right:8px">Home</a>
        <a href="{{ url_for('admin.logout') }}">Logout</a>
      </div>
    </div>
    <p class="muted">Signed in until: {{ until_local }}</p>
    {% if msg %}<div class="msg"><b>{{ msg }}</b></div>{% endif %}

    <form method="post" action="{{ url_for('admin.delete_latest') }}">
      <div class="row">
        <div>
          <label><b>Station</b></label><br>
          <select name="station" required>
            <option value="">Select</option>
            {% for s in stations %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
          </select>
        </div>
        <div>
          <label><b>Role</b></label><br>
          <select name="role" required>
            <option value="">Select</option>
            {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
          </select>
        </div>
        <div>
          <label><b>Stamp #</b></label><br>
          <input name="stamp" maxlength="4" pattern="\\d{4}" placeholder="####" required>
        </div>
        <div>
          <label><b>Type</b></label><br>
          <select name="kind" required>
            <option value="order">Order</option>
            <option value="reject">Reject</option>
            <option value="muda">Muda</option>
            <option value="reason:Bathroom">Reason: Bathroom</option>
            <option value="reason:Break">Reason: Break</option>
            <option value="reason:System Slow">Reason: System Slow</option>
          </select>
        </div>
      </div>
      <p class="muted">This deletes the <b>most recent</b> matching entry in <b>today's shift window</b>.</p>
      <button class="danger" type="submit">Delete latest</button>
    </form>
  </div>

  {% if preview %}
  <div class="card">
    <h3>Preview of the most‑recent match (if any)</h3>
    <table>
      <tr><th>When (local)</th><th>Type</th><th>Station</th><th>Role</th><th>Stamp</th><th>Detail</th></tr>
      {% if preview %}
      <tr>
        <td>{{ preview.when }}</td>
        <td>{{ preview.kind }}</td>
        <td>{{ preview.station }}</td>
        <td>{{ preview.role }}</td>
        <td>{{ preview.stamp }}</td>
        <td>{{ preview.detail }}</td>
      </tr>
      {% endif %}
    </table>
  </div>
  {% endif %}
</div>
"""

# ---------- routes ----------
@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if ADMIN_PASS is None or ADMIN_PASS == "":
        return render_template_string(LOGIN_HTML, have_pass=False, error=None)

    if request.method == "POST":
        if request.form.get("password", "") == ADMIN_PASS:
            _grant_auth()
            return redirect(url_for("admin.panel"))
        else:
            return render_template_string(LOGIN_HTML, have_pass=True, error="Wrong password.")
    return render_template_string(LOGIN_HTML, have_pass=True, error=None)

@admin_bp.route("/logout")
def logout():
    session.pop("admin_until", None)
    return redirect(url_for("admin.login"))

@admin_bp.route("/", methods=["GET"])
def panel():
    if not _is_authed():
        return redirect(url_for("admin.login"))

    until = session.get("admin_until")
    until_local = ""
    if until:
        try:
            until_local = datetime.fromisoformat(until).replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%I:%M %p").lstrip("0")
        except Exception:
            pass
    return render_template_string(
        PANEL_HTML,
        stations=STATIONS,
        roles=ROLES,
        msg=None,
        until_local=until_local,
        preview=None
    )

@admin_bp.route("/delete-latest", methods=["POST"])
def delete_latest():
    if not _is_authed():
        return redirect(url_for("admin.login"))

    station = (request.form.get("station") or "").strip()
    role    = (request.form.get("role") or "").strip()
    stamp   = (request.form.get("stamp") or "").strip()
    kind_in = (request.form.get("kind") or "").strip()

    if not (station and role and stamp and len(stamp)==4 and stamp.isdigit()):
        return redirect(url_for("admin.panel"))

    # today window (local)
    day = now_local().date()
    start_loc, end_loc = shift_bounds_local(day)
    start_utc = utc_from_local(start_loc)
    end_utc   = utc_from_local(end_loc)

    msg = "Nothing found to delete."
    preview = None

    if kind_in.startswith("reason:"):
        reason = kind_in.split(":",1)[1]
        q = (ReasonEvent.query
                .filter(ReasonEvent.station==station,
                        ReasonEvent.role==role,
                        ReasonEvent.stamp==stamp,
                        ReasonEvent.reason==reason,
                        ReasonEvent.ts_utc >= start_utc,
                        ReasonEvent.ts_utc < end_utc)
                .order_by(ReasonEvent.ts_utc.desc()))
        obj = q.first()
        if obj:
            preview = {
                "when": obj.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%m/%d %I:%M %p"),
                "kind": "Reason",
                "station": obj.station, "role": obj.role, "stamp": obj.stamp,
                "detail": reason
            }
            db.session.delete(obj)
            db.session.commit()
            msg = f"Deleted latest Reason: {reason} for {station}/{role}/Stamp {stamp}."
    else:
        # event: order / reject / muda
        q = (Event.query
                .filter(Event.station==station,
                        Event.role==role,
                        Event.stamp==stamp,
                        Event.kind==kind_in,
                        Event.ts_utc >= start_utc,
                        Event.ts_utc < end_utc)
                .order_by(Event.ts_utc.desc()))
        obj = q.first()
        if obj:
            preview = {
                "when": obj.ts_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%m/%d %I:%M %p"),
                "kind": obj.kind.title(),
                "station": obj.station, "role": obj.role, "stamp": obj.stamp,
                "detail": "-"
            }
            db.session.delete(obj)
            db.session.commit()
            msg = f"Deleted latest {kind_in.title()} for {station}/{role}/Stamp {stamp}."

    # show panel again with message + preview of the deleted row (or last match)
    until = session.get("admin_until")
    until_local = ""
    if until:
        try:
            until_local = datetime.fromisoformat(until).replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ).strftime("%I:%M %p").lstrip("0")
        except Exception:
            pass
    return render_template_string(
        PANEL_HTML,
        stations=STATIONS,
        roles=ROLES,
        msg=msg,
        until_local=until_local,
        preview=preview
    )
