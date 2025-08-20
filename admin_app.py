# admin_app.py
# Manager panel under /admin
# Lets managers add OR remove counts for a specific hour in today's shift.

from flask import Blueprint, render_template_string, request, redirect, url_for, session
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os

# ---- DO NOT import from app.py here (prevents circular import) ----
# We will "wire up" references from app.py by calling init_app(...) below.

# These globals will be set by init_app(globals_from_app)
db = Event = ReasonEvent = None
STATIONS = ROLES = REASONS = None            # <-- added REASONS
now_local = utc_from_local = shift_bounds_local = fixed_hour_labels = None

# -------- Config / Blueprint --------
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
TZ = ZoneInfo("America/Chicago")
ADMIN_PASS = os.environ.get("ADMIN_PASS")  # set on Render -> Environment
SESSION_HOURS = 2  # managers stay signed-in for N hours

# -------- wiring function (called from app.py AFTER everything is defined) ---
def init_app(ctx: dict) -> None:
    """
    Call this from app.py near the bottom, AFTER db/models/helpers exist:
        import admin_app
        admin_app.init_app(globals())
        app.register_blueprint(admin_app.admin_bp)
    """
    global db, Event, ReasonEvent, STATIONS, ROLES, REASONS          # <-- include REASONS
    global now_local, utc_from_local, shift_bounds_local, fixed_hour_labels

    db = ctx["db"]
    Event = ctx["Event"]
    ReasonEvent = ctx["ReasonEvent"]
    STATIONS = ctx["STATIONS"]
    ROLES = ctx["ROLES"]
    REASONS = ctx.get("REASONS", [])          # <-- pull REASONS from app.py if present
    now_local = ctx["now_local"]
    utc_from_local = ctx["utc_from_local"]
    shift_bounds_local = ctx["shift_bounds_local"]
    fixed_hour_labels = ctx["fixed_hour_labels"]

# -------- tiny auth helpers --------
def _is_authed() -> bool:
    until = session.get("admin_until")
    if not until:
        return False
    try:
        # value stored in UTC
        return datetime.utcnow() < datetime.fromisoformat(until)
    except Exception:
        return False

def _require_auth():
    if not _is_authed():
        return redirect(url_for("admin.login"))

def _grant_auth():
    session["admin_until"] = (datetime.utcnow() + timedelta(hours=SESSION_HOURS)).isoformat()

# -------- templates (inline) --------
LOGIN_HTML = """
<!doctype html><title>Manager Login</title>
<style>
  body{font-family:system-ui,Segoe UI,Arial;margin:40px;color:#111}
  .card{max-width:420px;margin:auto;border:1px solid #e5e7eb;border-radius:12px;padding:18px;box-shadow:0 8px 16px rgba(2,6,23,.06)}
  input,button{font-size:16px;padding:10px;width:100%;box-sizing:border-box;border-radius:10px;border:1px solid #cbd5e1}
  button{background:#2563eb;color:#fff;border:0;font-weight:700;margin-top:10px;cursor:pointer}
  .err{color:#b91c1c;margin:8px 0}
</style>
<div class="card">
  <h2>Manager Login</h2>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  {% if not have_pass %}
    <p>ADMIN_PASS is not set on the server. Ask IT to add it in Render → Environment.</p>
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
<!doctype html><title>Manager Panel</title>
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

    <form method="post" action="{{ url_for('admin.adjust_hour') }}">
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
            {% for key, label in reason_type_options %}
              <option value="{{ key }}">{{ label }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label><b>Hour</b></label><br>
          <select name="hour_ix" required>
            {% for ix, lbl in hour_labels %}
              <option value="{{ ix }}">{{ lbl }}</option>
            {% endfor %}
          </select>
        </div>
        <div>
          <label><b>Action</b></label><br>
          <select name="action" required>
            <option value="add">Add</option>
            <option value="remove">Remove</option>
          </select>
        </div>
        <div>
          <label><b>Count</b></label><br>
          <input name="count" type="number" min="1" max="99" value="1" required>
        </div>
      </div>
      <p class="muted">Adjusts only within the selected hour of <b>today's shift</b>.</p>
      <button type="submit" class="danger">Apply</button>
    </form>
  </div>
</div>
"""

# -------- Routes --------
@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if not ADMIN_PASS:
        return render_template_string(LOGIN_HTML, have_pass=False, error=None)
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASS:
            _grant_auth()
            return redirect(url_for("admin.panel"))
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

    until_local = ""
    until = session.get("admin_until")
    if until:
        try:
            until_local = (
                datetime.fromisoformat(until)
                .replace(tzinfo=ZoneInfo("UTC"))
                .astimezone(TZ)
                .strftime("%I:%M %p")
                .lstrip("0")
            )
        except Exception:
            pass

    # Build reason options from REASONS so the dropdown shows Bathroom/Break/System Slow, etc.
    reason_type_options = [(f"reason:{r}", f"Reason – {r}") for r in (REASONS or [])]

    return render_template_string(
        PANEL_HTML,
        stations=STATIONS,
        roles=ROLES,
        hour_labels=list(enumerate(fixed_hour_labels(now_local().date()))),
        reason_type_options=reason_type_options,   # <-- pass to template
        msg=None,
        until_local=until_local,
    )

@admin_bp.route("/adjust", methods=["POST"])
def adjust_hour():
    """Add or remove N items of kind within the chosen hour window for today."""
    if not _is_authed():
        return redirect(url_for("admin.login"))

    # pull form
    station = (request.form.get("station") or "").strip()
    role    = (request.form.get("role") or "").strip()
    stamp   = (request.form.get("stamp") or "").strip()
    kind    = (request.form.get("kind") or "").strip()      # order/reject/muda or reason:XYZ
    action  = (request.form.get("action") or "").strip()    # add/remove
    count_s = (request.form.get("count") or "1").strip()
    hour_ix_s = (request.form.get("hour_ix") or "").strip()

    # validate
    try:
        count = max(1, min(99, int(count_s)))
        hour_ix = int(hour_ix_s)
    except Exception:
        return redirect(url_for("admin.panel"))

    if not (station and role and stamp and len(stamp) == 4 and stamp.isdigit()):
        return redirect(url_for("admin.panel"))
    if not (kind in ("order", "reject", "muda") or kind.startswith("reason:")):  # <-- allow reasons
        return redirect(url_for("admin.panel"))
    if action not in ("add", "remove"):
        return redirect(url_for("admin.panel"))

    # window for selected hour of today's shift
    today = now_local().date()
    start_loc, end_loc = shift_bounds_local(today)  # overall shift (kept for context)
    labels = fixed_hour_labels(today)
    if not (0 <= hour_ix < len(labels)):
        return redirect(url_for("admin.panel"))

    # derive hour window in local time
    hour_start_loc = start_loc + timedelta(hours=hour_ix)
    hour_end_loc   = hour_start_loc + timedelta(hours=1)

    start_utc = utc_from_local(hour_start_loc)
    end_utc   = utc_from_local(hour_end_loc)

    msg = "No change."

    # --- NEW: handle reason adjustments ---
    if kind.startswith("reason:"):
        reason = kind.split(":", 1)[1]

        if action == "remove":
            removed = 0
            q = (ReasonEvent.query
                 .filter(ReasonEvent.station == station,
                         ReasonEvent.role == role,
                         ReasonEvent.stamp == stamp,
                         ReasonEvent.reason == reason,
                         ReasonEvent.ts_utc >= start_utc,
                         ReasonEvent.ts_utc <  end_utc)
                 .order_by(ReasonEvent.ts_utc.desc()))
            for obj in q.limit(count).all():
                db.session.delete(obj)
                removed += 1
            if removed:
                db.session.commit()
                msg = f"Removed {removed} reason(s) '{reason}' in {labels[hour_ix]}."
        else:
            added = 0
            for _ in range(count):
                db.session.add(ReasonEvent(
                    station=station, role=role, stamp=stamp,
                    reason=reason, ts_utc=start_utc + timedelta(minutes=1)  # inside the hour
                ))
                added += 1
            if added:
                db.session.commit()
                msg = f"Added {added} reason(s) '{reason}' in {labels[hour_ix]}."

        # return panel after reason handling
        until_local = ""
        until = session.get("admin_until")
        if until:
            try:
                until_local = (
                    datetime.fromisoformat(until)
                    .replace(tzinfo=ZoneInfo("UTC"))
                    .astimezone(TZ)
                    .strftime("%I:%M %p")
                    .lstrip("0")
                )
            except Exception:
                pass

        return render_template_string(
            PANEL_HTML,
            stations=STATIONS,
            roles=ROLES,
            hour_labels=list(enumerate(labels)),
            reason_type_options=[(f"reason:{r}", f"Reason – {r}") for r in (REASONS or [])],
            msg=msg,
            until_local=until_local,
        )
    # --- END reason branch ---

    # queries for normal events (order/reject/muda)
    q_events = (
        Event.query.filter(
            Event.station == station,
            Event.role == role,
            Event.stamp == stamp,
            Event.kind == kind,
            Event.ts_utc >= start_utc,
            Event.ts_utc < end_utc,
        )
        .order_by(Event.ts_utc.desc())
    )

    if action == "remove":
        removed = 0
        for obj in q_events.limit(count).all():
            db.session.delete(obj)
            removed += 1
        if removed:
            db.session.commit()
            msg = f"Removed {removed} {kind}(s) in {labels[hour_ix]}."
    else:
        # add
        added = 0
        for _ in range(count):
            ev = Event(
                station=station,
                role=role,
                stamp=stamp,
                kind=kind,
                ts_utc=start_utc + timedelta(minutes=1),  # place inside the hour
            )
            db.session.add(ev)
            added += 1
        if added:
            db.session.commit()
            msg = f"Added {added} {kind}(s) in {labels[hour_ix]}."

    # back to panel with message
    until_local = ""
    until = session.get("admin_until")
    if until:
        try:
            until_local = (
                datetime.fromisoformat(until)
                .replace(tzinfo=ZoneInfo("UTC"))
                .astimezone(TZ)
                .strftime("%I:%M %p")
                .lstrip("0")
            )
        except Exception:
            pass

    return render_template_string(
        PANEL_HTML,
        stations=STATIONS,
        roles=ROLES,
        hour_labels=list(enumerate(labels)),
        reason_type_options=[(f"reason:{r}", f"Reason – {r}") for r in (REASONS or [])],
        msg=msg,
        until_local=until_local,
    )
