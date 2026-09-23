"""Review web app -- multi-user, role-based (read / write / admin).

Run directly for local testing, or via gunicorn / the review container in
production. See README.md "Multi-user accounts and roles" for setup.
"""
from __future__ import annotations
from app.logging_setup import configure_logging
configure_logging()

import logging
import secrets
import time
from flask import Flask, render_template_string, request, redirect, session, url_for, abort, flash
from app.review_store import list_pending, resolve
from app.user_store import (
    verify_login, list_users, create_user, set_password, set_role, set_active,
    get_user, ensure_seeded, ROLE_RANK,
)
from app.auth import login_required
from app.audit_reader import get_summary
from app.log_reader import get_recent_lines
from app.policy_store import get_policy, update_policy
from app.logger import log_case
from app.config import FLASK_SECRET_KEY, COOKIE_SECURE

logger = logging.getLogger("review_app")

app = Flask(__name__)
ensure_seeded()
app.secret_key = FLASK_SECRET_KEY or secrets.token_hex(32)
if not FLASK_SECRET_KEY:
    print("WARNING: FLASK_SECRET_KEY not set in .env -- using a random one "
          "for this run only. Every restart will log everyone out. Set a "
          "fixed value before running this unattended.")

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
)

# Minimal brute-force guard: 5 failed attempts per IP locks that IP out for
# 5 minutes. In-memory only (resets on restart) -- fine for a small internal
# tool, swap for something persistent (e.g. Redis) if this sees real traffic.
_failed_attempts: dict[str, tuple[int, float]] = {}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def _is_locked_out(ip: str) -> bool:
    count, last = _failed_attempts.get(ip, (0, 0.0))
    return count >= MAX_ATTEMPTS and time.time() - last < LOCKOUT_SECONDS


def _record_failure(ip: str) -> None:
    count, _ = _failed_attempts.get(ip, (0, 0.0))
    _failed_attempts[ip] = (count + 1, time.time())


def _clear_failures(ip: str) -> None:
    _failed_attempts.pop(ip, None)


def _csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def _check_csrf():
    if request.form.get("csrf_token") != session.get("csrf_token"):
        abort(400, "Form expired or invalid -- go back, refresh, and try again.")


# ============================== STYLE ==============================

STYLE = """
  :root{
    --ink:#0A1622; --panel:#0F2033; --panel2:#132840;
    --line:#2E5068; --line-bright:#4F86A0;
    --cyan:#7FD8EA; --amber:#E8A548; --red:#DD6E63; --green:#6FBF8B;
    --text:#E9F1F4; --text-dim:#93ACBC;
  }
  *{box-sizing:border-box;}
  body{font-family:-apple-system,'Segoe UI',sans-serif;background:var(--ink);color:var(--text);
       margin:0;padding:0;}
  a{color:inherit;}
  h1{font-size:21px;font-weight:600;}
  h2{font-size:16px;font-weight:600;margin-bottom:4px;}
  .wrap{max-width:860px;margin:0 auto;padding:0 24px 60px;}
  .wrap.narrow{max-width:440px;}

  /* navbar */
  .navbar{display:flex;align-items:center;justify-content:space-between;padding:16px 24px;
          border-bottom:1px solid var(--line);margin-bottom:28px;}
  .navbar .brand{font-weight:700;font-size:15px;letter-spacing:-0.01em;display:flex;align-items:center;gap:8px;}
  .navbar .brand .sw{width:9px;height:9px;border-radius:2px;background:var(--cyan);}
  .navlinks{display:flex;gap:6px;align-items:center;}
  .navlinks a{font-size:13px;color:var(--text-dim);text-decoration:none;padding:7px 12px;border-radius:5px;}
  .navlinks a:hover{background:var(--panel2);color:var(--text);}
  .navlinks a.active{background:var(--panel2);color:var(--cyan);}
  .userchip{display:flex;align-items:center;gap:10px;margin-left:14px;padding-left:14px;border-left:1px solid var(--line);}
  .userchip .name{font-size:13px;color:var(--text);}
  .logout{font-size:12px;color:var(--text-dim);text-decoration:none;border:1px solid var(--line);padding:5px 10px;border-radius:4px;}
  .logout:hover{color:var(--red);border-color:var(--red);}

  /* badges */
  .badge{font-family:'IBM Plex Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:.04em;
         padding:3px 8px;border-radius:3px;display:inline-block;}
  .badge-admin{background:rgba(232,165,72,0.15);color:var(--amber);border:1px solid rgba(232,165,72,0.4);}
  .badge-write{background:rgba(127,216,234,0.15);color:var(--cyan);border:1px solid rgba(127,216,234,0.4);}
  .badge-read{background:rgba(147,172,188,0.15);color:var(--text-dim);border:1px solid rgba(147,172,188,0.3);}
  .badge-inactive{background:rgba(221,110,99,0.12);color:var(--red);border:1px solid rgba(221,110,99,0.35);}

  /* flash messages */
  .flash{padding:11px 16px;border-radius:5px;font-size:13.5px;margin-bottom:18px;}
  .flash-success{background:rgba(111,191,139,0.1);border:1px solid var(--green);color:var(--green);}
  .flash-error{background:rgba(221,110,99,0.1);border:1px solid var(--red);color:var(--red);}

  /* stat cards */
  .stats{display:flex;gap:12px;margin-bottom:24px;}
  .stat{flex:1;border:1px solid var(--line);border-radius:6px;padding:14px 16px;background:var(--panel);}
  .stat .n{font-size:24px;font-weight:700;}
  .stat .l{font-size:11.5px;color:var(--text-dim);margin-top:2px;}

  /* case cards */
  .empty{color:var(--text-dim);padding:40px 0;text-align:center;}
  .case{border:1px solid var(--line);border-radius:6px;padding:20px;margin-bottom:18px;background:var(--panel);}
  .case.needs-draft{border-left:3px solid var(--amber);}
  .flag-banner{background:rgba(232,165,72,0.1);border:1px solid var(--amber);color:var(--amber);
               font-size:12.5px;padding:9px 12px;border-radius:4px;margin-bottom:14px;}
  .meta{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--cyan);margin-bottom:10px;}
  .field{margin-bottom:12px;}
  .label{font-size:11px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;}
  .original{white-space:pre-wrap;font-size:13.5px;color:var(--text-dim);background:#081221;padding:10px;border-radius:4px;}
  .readonly-note{font-size:12px;color:var(--text-dim);font-style:italic;}

  /* forms */
  textarea{width:100%;min-height:140px;background:#081221;color:var(--text);border:1px solid var(--line);
           border-radius:4px;padding:10px;font-family:inherit;font-size:13.5px;box-sizing:border-box;}
  input[type=text],input[type=password],select{width:100%;background:#081221;color:var(--text);border:1px solid var(--line);
           border-radius:4px;padding:9px 10px;font-family:inherit;font-size:13.5px;box-sizing:border-box;}
  label.formlabel{font-size:12px;color:var(--text-dim);display:block;margin-bottom:5px;margin-top:12px;}

  .actions{display:flex;gap:10px;margin-top:14px;}
  button{font-family:inherit;font-size:13px;padding:8px 16px;border-radius:5px;border:none;cursor:pointer;}
  .btn-primary{background:var(--green);color:var(--ink);font-weight:600;}
  .btn-danger{background:transparent;border:1px solid var(--red);color:var(--red);}
  .btn-secondary{background:transparent;border:1px solid var(--line-bright);color:var(--text);}
  .btn-small{padding:5px 11px;font-size:12px;}

  /* card panel (login, account, admin forms) */
  .panel{border:1px solid var(--line);border-radius:6px;padding:24px;background:var(--panel);margin-bottom:20px;}
  .panel-tight{padding:16px 18px;}

  /* admin table */
  table{width:100%;border-collapse:collapse;}
  th{text-align:left;font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--cyan);
     text-transform:uppercase;letter-spacing:.04em;padding:9px 10px;border-bottom:1px solid var(--line-bright);}
  td{padding:11px 10px;border-bottom:1px solid var(--line);font-size:13px;vertical-align:middle;}
  tr:last-child td{border-bottom:none;}
  .inline-form{display:flex;gap:6px;align-items:center;}
  .user-meta{font-size:11px;color:var(--text-dim);}

  /* governance dashboard */
  .stat.warn .n{color:var(--amber);}
  .stat.danger .n{color:var(--red);}
  .muted{color:var(--text-dim);font-size:12.5px;}
  .checkbox-row{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px;}
  .checkbox-row input{width:auto;}
  .warn-banner{background:rgba(232,165,72,0.1);border:1px solid var(--amber);color:var(--amber);
               font-size:13px;padding:10px 14px;border-radius:5px;margin-bottom:16px;}
  .activity-row{font-size:12.5px;padding:9px 0;border-top:1px solid var(--line);font-family:'IBM Plex Mono',monospace;}
  .activity-row:first-child{border-top:none;}
  .activity-row .stage{color:var(--cyan);}
  .code-textarea{font-family:'IBM Plex Mono',monospace;min-height:130px;}

  /* live logs page */
  .log-line{padding:4px 0;border-top:1px solid var(--line);white-space:pre-wrap;word-break:break-word;
            font-family:'IBM Plex Mono',monospace;font-size:11.5px;}
  .log-line:first-child{border-top:none;}
  .log-info{color:var(--text-dim);}
  .log-debug{color:var(--text-dim);opacity:0.55;}
  .log-warning{color:var(--amber);}
  .log-error,.log-critical{color:var(--red);}
  .log-panel{max-height:70vh;overflow-y:auto;}
"""


def navbar_html(active: str) -> str:
    role = session.get("role", "")
    username = session.get("username", "")
    admin_link = '<a href="/admin/users" class="{}">Admin</a>'.format("active" if active == "admin" else "")
    return f"""
    <div class="navbar">
      <div class="brand"><span class="sw"></span>Email Agent</div>
      <div class="navlinks">
        <a href="/" class="{'active' if active == 'queue' else ''}">Review Queue</a>
        <a href="/governance" class="{'active' if active == 'governance' else ''}">Governance</a>
        <a href="/logs" class="{'active' if active == 'logs' else ''}">Logs</a>
        <a href="/account" class="{'active' if active == 'account' else ''}">My Account</a>
        {admin_link if role == "admin" else ""}
        <div class="userchip">
          <span class="name">{username}</span>
          <span class="badge badge-{role}">{role}</span>
          <a class="logout" href="/logout">Log out</a>
        </div>
      </div>
    </div>
    """


def flash_html() -> str:
    return """
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% for category, msg in messages %}
        <div class="flash flash-{{ category }}">{{ msg }}</div>
      {% endfor %}
    {% endwith %}
    """


HEAD_TEMPLATE = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__</title><style>""" + STYLE + """</style></head><body>"""


def make_head(title: str) -> str:
    return HEAD_TEMPLATE.replace("__TITLE__", title)

# ============================== LOGIN ==============================

LOGIN_PAGE = make_head("Email Agent -- Login") + """
  <div class="wrap narrow">
    <div class="panel" style="margin-top:80px;">
      <h1 style="margin-bottom:18px;">Review page login</h1>
      """ + flash_html() + """
      {% if error %}<div class="flash flash-error">{{ error }}</div>{% endif %}
      <form method="post">
        <label class="formlabel">Username</label>
        <input type="text" name="username" autofocus>
        <label class="formlabel">Password</label>
        <input type="password" name="password">
        <div class="actions"><button type="submit" class="btn-primary">Log in</button></div>
      </form>
    </div>
  </div>
</body></html>
"""

# ============================== QUEUE ==============================

QUEUE_PAGE = make_head("Email Agent -- Review Queue") + """
  <div class="wrap">
    __NAVBAR__
    """ + flash_html() + """
    <div class="stats">
      <div class="stat"><div class="n">{{ pending|length }}</div><div class="l">Open cases</div></div>
      <div class="stat"><div class="n">{{ needs_draft_count }}</div><div class="l">Need a manual draft</div></div>
      <div class="stat"><div class="n">{{ ai_draft_count }}</div><div class="l">AI-drafted, awaiting approval</div></div>
    </div>
    <h1 style="margin-bottom:16px;">Pending review</h1>
    {% if not pending %}
      <p class="empty">Nothing waiting right now. New cases will appear here automatically -- refresh to check.</p>
    {% endif %}
    {% for case_id, entry in pending %}
    <div class="case{% if entry.flag %} needs-draft{% endif %}">
      <div class="meta">CASE {{ case_id }} &middot; from {{ entry.from_addr }} &middot; {{ entry.classification }}</div>
      {% if entry.flag %}
      <div class="flag-banner">⚠ {{ entry.flag }}</div>
      {% endif %}
      <div class="field">
        <div class="label">Original email</div>
        <div class="original">{{ entry.original_email }}</div>
      </div>
      {% if can_write %}
      <form method="post" action="{{ url_for('resolve_case', case_id=case_id) }}" onsubmit="return confirmSend(event, this)">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <div class="field">
          <div class="label">{% if entry.flag %}Write the reply{% else %}Drafted reply (editable){% endif %}</div>
          <textarea name="draft" placeholder="{% if entry.flag %}No AI draft -- write the reply to send here{% endif %}">{{ entry.draft }}</textarea>
        </div>
        <div class="actions">
          <button type="submit" name="decision" value="approved" class="btn-primary" onclick="window.__lastDecision='approved'">Approve &amp; send</button>
          <button type="submit" name="decision" value="rejected" class="btn-danger" onclick="window.__lastDecision='rejected'">Reject</button>
        </div>
      </form>
      {% else %}
      <div class="field">
        <div class="label">Drafted reply</div>
        <div class="original">{{ entry.draft or "(no draft yet)" }}</div>
      </div>
      <p class="readonly-note">View-only access -- ask an admin for write access to approve or reject cases.</p>
      {% endif %}
    </div>
    {% endfor %}
  </div>
  <script>
    function confirmSend(evt, form){
      var draft = form.querySelector('textarea[name="draft"]').value.trim();
      if(window.__lastDecision === 'approved' && draft.length === 0){
        alert('Write a reply before approving -- the draft box is empty.');
        return false;
      }
      return true;
    }
  </script>
</body></html>
"""

# ============================== ACCOUNT ==============================

ACCOUNT_PAGE = make_head("Email Agent -- My Account") + """
  <div class="wrap narrow">
    __NAVBAR__
    """ + flash_html() + """
    <div class="panel">
      <h1>My account</h1>
      <p style="font-size:13px;color:var(--text-dim);margin:6px 0 4px;">Signed in as <b style="color:var(--text)">{{ username }}</b> &middot; <span class="badge badge-{{ role }}">{{ role }}</span></p>
    </div>
    <div class="panel">
      <h2>Change password</h2>
      <form method="post">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label class="formlabel">Current password</label>
        <input type="password" name="current_password">
        <label class="formlabel">New password (min 10 characters)</label>
        <input type="password" name="new_password">
        <label class="formlabel">Confirm new password</label>
        <input type="password" name="confirm_password">
        <div class="actions"><button type="submit" class="btn-primary">Update password</button></div>
      </form>
    </div>
  </div>
</body></html>
"""

# ============================== ADMIN: USERS ==============================

ADMIN_USERS_PAGE = make_head("Email Agent -- Manage Users") + """
  <div class="wrap">
    __NAVBAR__
    """ + flash_html() + """
    <h1 style="margin-bottom:16px;">Manage users</h1>

    <div class="panel">
      <table>
        <tr><th>Username</th><th>Role</th><th>Status</th><th>Created</th><th>Actions</th></tr>
        {% for uname, u in users %}
        <tr>
          <td>{{ uname }}{% if uname == current_username %} <span class="user-meta">(you)</span>{% endif %}</td>
          <td><span class="badge badge-{{ u.role }}">{{ u.role }}</span></td>
          <td>{% if u.active %}<span class="badge badge-write">active</span>{% else %}<span class="badge badge-inactive">inactive</span>{% endif %}</td>
          <td class="user-meta">{{ u.created_at[:10] }}</td>
          <td>
            <details>
              <summary style="cursor:pointer;font-size:12px;color:var(--cyan);">Manage</summary>
              <div style="margin-top:10px;display:flex;flex-direction:column;gap:8px;">
                <form method="post" action="{{ url_for('admin_set_role', username=uname) }}" class="inline-form">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <select name="role" style="width:auto;">
                    <option value="read" {% if u.role=='read' %}selected{% endif %}>read</option>
                    <option value="write" {% if u.role=='write' %}selected{% endif %}>write</option>
                    <option value="admin" {% if u.role=='admin' %}selected{% endif %}>admin</option>
                  </select>
                  <button type="submit" class="btn-secondary btn-small">Set role</button>
                </form>
                <form method="post" action="{{ url_for('admin_reset_password', username=uname) }}" class="inline-form">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  <input type="text" name="new_password" placeholder="New password (min 10 chars)" style="width:220px;">
                  <button type="submit" class="btn-secondary btn-small">Reset password</button>
                </form>
                <form method="post" action="{{ url_for('admin_toggle_active', username=uname) }}">
                  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
                  {% if u.active %}
                    <button type="submit" class="btn-danger btn-small">Deactivate</button>
                  {% else %}
                    <button type="submit" class="btn-secondary btn-small">Reactivate</button>
                  {% endif %}
                </form>
              </div>
            </details>
          </td>
        </tr>
        {% endfor %}
      </table>
    </div>

    <div class="panel panel-tight">
      <h2>Create a new user</h2>
      <form method="post" action="{{ url_for('admin_create_user') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label class="formlabel">Username</label>
        <input type="text" name="username">
        <label class="formlabel">Initial password (min 10 characters)</label>
        <input type="text" name="password">
        <label class="formlabel">Role</label>
        <select name="role">
          <option value="read">read -- view only</option>
          <option value="write" selected>write -- can review &amp; approve</option>
          <option value="admin">admin -- full access incl. user management</option>
        </select>
        <div class="actions"><button type="submit" class="btn-primary">Create user</button></div>
      </form>
    </div>
  </div>
</body></html>
"""


# ============================== GOVERNANCE ==============================

GOVERNANCE_PAGE = make_head("Email Agent -- Governance") + """
  <div class="wrap">
    __NAVBAR__
    """ + flash_html() + """
    <h1 style="margin-bottom:6px;">Governance dashboard</h1>
    <p class="muted" style="margin-bottom:18px;">{{ summary.window_note }}</p>

    <div class="stats">
      <div class="stat"><div class="n">{{ summary.sent }}</div><div class="l">Emails sent</div></div>
      <div class="stat"><div class="n">{{ summary.rejected }}</div><div class="l">Rejected by reviewer</div></div>
      <div class="stat"><div class="n">{{ summary.escalated }}</div><div class="l">Escalated to human</div></div>
      <div class="stat{% if summary.quarantined %} warn{% endif %}"><div class="n">{{ summary.quarantined }}</div><div class="l">Quarantined by guardrails</div></div>
      <div class="stat"><div class="n">{{ summary.approval_rate if summary.approval_rate is not none else '--' }}{% if summary.approval_rate is not none %}%{% endif %}</div><div class="l">Approval rate</div></div>
    </div>

    {% if summary.quarantine_reasons %}
    <div class="panel panel-tight">
      <h2>Top quarantine reasons</h2>
      <table>
        {% for reason, count in summary.quarantine_reasons %}
        <tr><td>{{ reason }}</td><td style="width:60px;text-align:right;">{{ count }}</td></tr>
        {% endfor %}
      </table>
    </div>
    {% endif %}

    <div class="panel">
      <h2>Recent activity</h2>
      {% if not summary.recent %}
        <p class="empty">No activity logged yet.</p>
      {% endif %}
      {% for e in summary.recent %}
      <div class="activity-row">{{ e.timestamp[:19] }} &middot; <span class="stage">{{ e.stage }}</span>{% if e.case_id %} &middot; {{ e.case_id }}{% endif %}{% if e.resolved_by %} &middot; by {{ e.resolved_by }}{% endif %}{% if e.reason %} &middot; {{ e.reason }}{% endif %}</div>
      {% endfor %}
    </div>

    {% if role == "admin" %}
    <div class="panel">
      <h2>Guardrails policy</h2>
      <p class="muted" style="margin-bottom:10px;">Changes apply to the very next email -- no restart needed. Every change here is logged to the audit trail.</p>
      {% if not policy.injection_patterns %}
      <div class="warn-banner">⚠ No injection patterns configured -- prompt-injection detection is currently OFF.</div>
      {% endif %}
      <form method="post" action="{{ url_for('admin_update_guardrails') }}">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label class="formlabel">Prompt-injection patterns (one regex per line)</label>
        <textarea name="patterns" class="code-textarea">{{ policy.injection_patterns|join('\n') }}</textarea>

        <label class="formlabel">Quarantine threshold -- max email length (characters)</label>
        <input type="text" name="max_email_length" value="{{ policy.max_email_length }}" style="max-width:160px;">

        <div class="checkbox-row"><input type="checkbox" name="redact_email" {% if policy.redact_email %}checked{% endif %}> Redact email addresses in logs</div>
        <div class="checkbox-row"><input type="checkbox" name="redact_phone" {% if policy.redact_phone %}checked{% endif %}> Redact phone numbers in logs</div>
        <div class="checkbox-row"><input type="checkbox" name="redact_card" {% if policy.redact_card %}checked{% endif %}> Redact card-like numbers in logs</div>

        <p class="muted" style="margin-top:10px;">Last updated: {{ policy.updated_at[:19] if policy.updated_at else "never (using defaults)" }}{% if policy.updated_by %} by {{ policy.updated_by }}{% endif %}</p>

        <div class="actions"><button type="submit" class="btn-primary">Save guardrails policy</button></div>
      </form>
    </div>
    {% endif %}
  </div>
</body></html>
"""

# ============================== LIVE LOGS ==============================

LOGS_PAGE = make_head("Email Agent -- Logs") + """
  <div class="wrap">
    __NAVBAR__
    <div class="topbar" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
      <h1>Live agent logs</h1>
      <a href="/logs" class="btn-secondary btn-small" style="text-decoration:none;">Refresh</a>
    </div>
    <p class="muted" style="margin-bottom:16px;">Last {{ lines|length }} lines from the pipeline and review app, most recent first. Same detail as <code>docker compose logs</code>, without needing server access.</p>
    <div class="panel panel-tight log-panel">
      {% if not lines %}
        <p class="empty">No log activity yet.</p>
      {% endif %}
      {% for l in lines %}
      <div class="log-line log-{{ l.level|lower }}">{{ l.raw }}</div>
      {% endfor %}
    </div>
  </div>
</body></html>
"""


# ============================== ROUTES ==============================

@app.route("/login", methods=["GET", "POST"])
def login():
    ip = request.remote_addr
    error = None
    if request.method == "POST":
        if _is_locked_out(ip):
            error = "Too many failed attempts. Try again in a few minutes."
            logger.warning("Login blocked -- IP is locked out: ip=%s", ip)
        else:
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            user = verify_login(username, password)
            if user:
                _clear_failures(ip)
                session["logged_in"] = True
                session["username"] = username
                session["role"] = user["role"]
                session["csrf_token"] = secrets.token_hex(16)
                logger.info("Login success: user=%s role=%s ip=%s", username, user["role"], ip)
                return redirect(request.args.get("next") or url_for("index"))
            _record_failure(ip)
            logger.warning("Login FAILED: user=%s ip=%s", username, ip)
            error = "Invalid username or password."
    return render_template_string(LOGIN_PAGE, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required(min_role="read")
def index():
    pending = list_pending()
    needs_draft_count = sum(1 for _, e in pending if e.get("flag"))
    ai_draft_count = len(pending) - needs_draft_count
    can_write = ROLE_RANK.get(session.get("role", "read"), 0) >= ROLE_RANK["write"]
    html = QUEUE_PAGE.replace("__NAVBAR__", navbar_html("queue"))
    return render_template_string(
        html, pending=pending, csrf_token=_csrf_token(),
        needs_draft_count=needs_draft_count, ai_draft_count=ai_draft_count,
        can_write=can_write,
    )


@app.route("/governance")
@login_required(min_role="read")
def governance():
    summary = get_summary()
    policy = get_policy()
    html = GOVERNANCE_PAGE.replace("__NAVBAR__", navbar_html("governance"))
    return render_template_string(
        html, summary=summary, policy=policy, role=session.get("role"), csrf_token=_csrf_token(),
    )


@app.route("/logs")
@login_required(min_role="read")
def logs_view():
    lines = get_recent_lines()
    html = LOGS_PAGE.replace("__NAVBAR__", navbar_html("logs"))
    return render_template_string(html, lines=lines)


@app.route("/admin/guardrails/update", methods=["POST"])
@login_required(min_role="admin")
def admin_update_guardrails():
    _check_csrf()
    patterns = [line for line in request.form.get("patterns", "").splitlines()]
    try:
        max_len = int(request.form.get("max_email_length", "6000"))
    except ValueError:
        flash("Max email length must be a number.", "error")
        return redirect(url_for("governance"))

    redact_email = request.form.get("redact_email") == "on"
    redact_phone = request.form.get("redact_phone") == "on"
    redact_card = request.form.get("redact_card") == "on"

    ok, msg = update_policy(patterns, max_len, redact_email, redact_phone, redact_card, updated_by=session["username"])
    flash(msg, "success" if ok else "error")
    logger.warning(
        "Admin action by %s: update guardrails policy (%d pattern(s), max_len=%s) -- %s",
        session["username"], len(patterns), max_len, "OK" if ok else "FAILED: " + msg,
    )
    if ok:
        # Guardrail policy changes are governance-relevant events -- record
        # them in the permanent audit trail, same as any other case event.
        log_case(
            f"policy-{int(time.time())}", stage="guardrails_policy_updated",
            updated_by=session["username"], pattern_count=len(patterns), max_email_length=max_len,
        )
    return redirect(url_for("governance"))


@app.route("/resolve/<case_id>", methods=["POST"])
@login_required(min_role="write")
def resolve_case(case_id):
    _check_csrf()
    decision = request.form.get("decision")
    edited_draft = request.form.get("draft")
    resolve(case_id, decision, edited_draft, resolved_by=session.get("username"))
    logger.info("Case %s resolved: decision=%s by=%s", case_id, decision, session.get("username"))
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required(min_role="read")
def account():
    if request.method == "POST":
        _check_csrf()
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        username = session["username"]
        if not verify_login(username, current_pw):
            flash("Current password is incorrect.", "error")
            logger.warning("Self password-change failed (wrong current password): user=%s", username)
        elif new_pw != confirm_pw:
            flash("New password and confirmation don't match.", "error")
        else:
            ok, msg = set_password(username, new_pw)
            flash(msg, "success" if ok else "error")
            if ok:
                logger.info("Password changed by user themself: user=%s", username)
        return redirect(url_for("account"))
    html = ACCOUNT_PAGE.replace("__NAVBAR__", navbar_html("account"))
    return render_template_string(html, username=session["username"], role=session["role"], csrf_token=_csrf_token())


@app.route("/admin/users")
@login_required(min_role="admin")
def admin_users():
    html = ADMIN_USERS_PAGE.replace("__NAVBAR__", navbar_html("admin"))
    return render_template_string(html, users=list_users(), current_username=session["username"], csrf_token=_csrf_token())


@app.route("/admin/users/create", methods=["POST"])
@login_required(min_role="admin")
def admin_create_user():
    _check_csrf()
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    role = request.form.get("role", "write")
    ok, msg = create_user(username, password, role, created_by=session["username"])
    flash(msg, "success" if ok else "error")
    logger.info("Admin action by %s: create user '%s' role=%s -- %s", session["username"], username, role, "OK" if ok else "FAILED: " + msg)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<username>/role", methods=["POST"])
@login_required(min_role="admin")
def admin_set_role(username):
    _check_csrf()
    role = request.form.get("role", "")
    ok, msg = set_role(username, role, acting_user=session["username"])
    flash(msg, "success" if ok else "error")
    logger.info("Admin action by %s: set role of '%s' to %s -- %s", session["username"], username, role, "OK" if ok else "FAILED: " + msg)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<username>/reset-password", methods=["POST"])
@login_required(min_role="admin")
def admin_reset_password(username):
    _check_csrf()
    new_password = request.form.get("new_password", "")
    ok, msg = set_password(username, new_password)
    flash(msg, "success" if ok else "error")
    # Never log the password itself -- only that a reset happened and by whom.
    logger.info("Admin action by %s: reset password for '%s' -- %s", session["username"], username, "OK" if ok else "FAILED: " + msg)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<username>/toggle-active", methods=["POST"])
@login_required(min_role="admin")
def admin_toggle_active(username):
    _check_csrf()
    user = get_user(username)
    if not user:
        flash("User not found.", "error")
    else:
        ok, msg = set_active(username, not user.get("active", True), acting_user=session["username"])
        flash(msg, "success" if ok else "error")
        logger.info("Admin action by %s: set active=%s for '%s' -- %s", session["username"], not user.get("active", True), username, "OK" if ok else "FAILED: " + msg)
    return redirect(url_for("admin_users"))


if __name__ == "__main__":
    # 127.0.0.1 even here -- see README for why public exposure goes
    # through nginx + gunicorn instead of this dev server directly.
    app.run(host="127.0.0.1", port=5000, debug=False)
