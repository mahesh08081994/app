# Email Auto-Response Agent -- Local MVP

A local, multi-agent email auto-responder: intake -> guardrails -> classify ->
retrieve -> draft -> **human review** -> send -> log. Runs entirely on your
machine via Ollama. Ships in dry-run mode so nothing sends until you turn it
on deliberately.

This is a working skeleton for learning and adapting, not a hardened
production deployment. See "Before you go live" at the bottom.

## Every case reaches the review page -- nothing goes silent

Quarantined and escalated emails no longer just get logged and forgotten.
Every email now reaches the review page one of two ways:

- **AI drafted a reply** -- shows with the draft pre-filled and editable,
  as before.
- **Quarantined or escalated** -- shows with an empty draft box, a
  placeholder prompt, and an amber warning banner explaining why (e.g.
  "Escalated by the classifier -- please write the reply yourself"). The
  card has an amber left border so it's visually distinct at a glance.

Write the reply yourself in the box and click **Approve & send** as
normal. Clicking Approve with an empty box is blocked client-side with a
prompt to write something first -- it won't silently send a blank email.

This also solves checking the mailbox directly to find unhandled email:
don't do that anymore -- opening a message there marks it "read" and hides
it from the poller. The review page is now the single place to see every
email that needs attention, handled or not.

## Two-tier knowledge grounding (company facts vs. general knowledge)

The drafting agent (`app/drafter.py`) now draws on two sources with
different trust levels, not just the knowledge base:

- **Company-specific facts** -- policies, pricing, refunds, shipping
  timelines, anything about a specific order -- must come **only** from
  the retrieved `CONTEXT` block (ChromaDB). The model is instructed to
  never guess these, and to defer to a human follow-up if the context
  doesn't cover it.
- **General, non-company-specific information** -- explaining a common
  term, general troubleshooting, translating a phrase -- the model may
  use its own general knowledge, since these aren't commitments made on
  the company's behalf.

**This is a deliberate, documented risk decision, not an oversight:** it
reintroduces a small amount of hallucination surface, deliberately scoped
to non-company facts only. Record this the same way as any other
governance decision in this project -- it's the kind of detail a security
or AI Act review would expect to see written down, not left implicit in
a prompt no one remembers changing.

## What you need to change before running

1. **Install Ollama and pull the model.**
   ```
   curl -fsSL https://ollama.com/install.sh | sh      # macOS/Linux
   # Windows: https://ollama.com/download
   ollama pull llama3.1:8b
   ```

2. **Create your `.env` file.**
   ```
   cp .env.example .env
   ```
   Then edit `.env` and fill in:
   - `IMAP_HOST` / `SMTP_HOST` -- your mail provider's server addresses
     (Gmail: `imap.gmail.com` / `smtp.gmail.com`. Outlook/Office365:
     `outlook.office365.com` / `smtp.office365.com`. Check your provider's
     docs if different.)
   - `IMAP_USER` / `SMTP_USER` -- the mailbox address itself.
   - `IMAP_PASS` / `SMTP_PASS` -- **an app-specific password, not your real
     account password.**
     - Gmail: Google Account -> Security -> 2-Step Verification -> App
       passwords. Requires 2FA to be turned on first.
     - Outlook: account.microsoft.com -> Security -> Advanced security
       options -> App passwords.
   - Leave `DRY_RUN=true` until you've completed the testing steps below.

3. **Create a Python virtual environment and install dependencies.**
   ```
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

4. **Replace the sample knowledge files with your real ones.**
   The three `.txt` files in `knowledge/` are placeholder examples.
   Delete or edit them, and add your actual FAQ, returns policy, shipping
   policy, or any other reference text customers ask about. Plain text
   files only for this version -- one topic per file works best.

5. **Build the knowledge base index.**
   ```
   python build_index.py
   ```
   Re-run this every time you change a file in `knowledge/`.

## Running unattended (no terminal or SSH session kept open)

Two things change for this: the pipeline needs to run in a loop instead of
once (`--mode poll`, added above), and both processes need to run as
**systemd services** rather than in a terminal -- that's what lets them
keep running after you log out of SSH, and restart automatically if the
server reboots or a process crashes.

**1. Install the service files** (edit the paths and `User=` inside each
file first to match your actual username and install location):
```
sudo cp deploy/email-agent-review.service /etc/systemd/system/
sudo cp deploy/email-agent-poller.service /etc/systemd/system/
sudo systemctl daemon-reload
```

**2. Enable and start both:**
```
sudo systemctl enable --now email-agent-review
sudo systemctl enable --now email-agent-poller
```
`enable` makes them start automatically on every server boot. `--now` also
starts them immediately.

**3. Check they're running:**
```
sudo systemctl status email-agent-review
sudo systemctl status email-agent-poller
```
Both should say `active (running)`. You can now close your SSH session
entirely -- they keep running.

**4. View logs any time (even after logging back in later):**
```
sudo journalctl -u email-agent-review -f
sudo journalctl -u email-agent-poller -f
```

**5. Reaching the review page from your browser.**
The service binds to `127.0.0.1` on the server only, on purpose -- it has
no login page, so it should never be open to the internet as-is. To view
it from your own laptop, open an SSH tunnel instead of changing the bind
address:
```
ssh -i your-key.pem -L 5000:localhost:5000 admin@your-server-ip
```
Leave that terminal open, then visit `http://localhost:5000` in your own
browser -- it forwards through the tunnel to the server. This is the
right way to access it for now. If you later want it reachable without a
tunnel (e.g. for a whole support team), that requires adding real
authentication and putting it behind HTTPS first -- see the checklist
below, don't just change `host="0.0.0.0"`.

From this point on, the only human action required anywhere in the system
is clicking Approve/Reject on that page -- everything else (checking the
inbox, classifying, retrieving, drafting, logging) runs on its own.

## Exposing the review page publicly

The review page now requires login (hashed password, not stored in plain
text) and has basic brute-force protection and CSRF-protected forms. That
covers the application layer. Getting it safely onto a public IP needs two
more pieces: **TLS** (so the login and every approved draft aren't sent in
plain text) and a **real WSGI server** (the Flask dev server used in
earlier steps is not built to face the internet directly -- gunicorn is).

**1. Generate your login credentials.**
```
python deploy/hash_password.py
```
Paste the two lines it prints (`REVIEW_PASSWORD_HASH=...` and
`FLASK_SECRET_KEY=...`) into `.env`. Pick a real username too if you don't
want to keep the `admin` default.

**2. Get a hostname pointing at this server -- or use your bare IP instead.**

*Recommended:* TLS certificates (the padlock, no warning) can only be
issued for a domain name, never a bare IP -- this is true everywhere, not
specific to this project. [DuckDNS](https://www.duckdns.org) gives you a
free hostname (`yourname.duckdns.org`) pointed at your IP in about 2
minutes, no domain purchase needed, and unlocks the warning-free setup in
steps 3-5 below as written.

*If you'd rather use the bare public IP with no DNS at all:* skip to
"IP-only setup (no domain, self-signed certificate)" further down instead
of steps 3-5 -- it works, but every visitor's browser will show a
"connection not private" warning, and a self-signed cert doesn't protect
against network-level impersonation the way a real one does. Fine for a
small internal tool with a handful of known reviewers; not something to
scale up to a public audience without revisiting.

**3. Lock down the firewall / EC2 security group.**
Open only **80** and **443** inbound. Do **not** open port 5000 --
gunicorn stays bound to `127.0.0.1`; nginx is the only public-facing edge.

**4. Install and configure nginx + certbot** (commands below are for
Amazon Linux 2023; adjust package manager if you're on a different
distro):
```
sudo dnf install -y nginx
sudo dnf install -y python3-certbot-nginx
sudo cp deploy/nginx-email-agent.conf /etc/nginx/conf.d/email-agent.conf
```
Edit `/etc/nginx/conf.d/email-agent.conf` and replace
`REPLACE_WITH_YOUR_DOMAIN` with your actual hostname from Step 2, then:
```
sudo systemctl enable --now nginx
sudo certbot --nginx -d yourname.duckdns.org
```
Certbot fetches a real certificate and rewrites the config's TLS lines
automatically -- you don't create certificates by hand.

**5. Switch the review service to gunicorn** (the updated
`deploy/email-agent-review.service` already does this -- re-copy it if
you installed the systemd services before this change):
```
sudo cp deploy/email-agent-review.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart email-agent-review
```

**6. Verify:**
- `https://yourname.duckdns.org` shows the login page with a valid padlock
- plain `http://` redirects to `https://` automatically
- `http://your-ec2-public-ip:5000` is **not** reachable at all (confirms
  the security group is actually blocking it)
- logging in with the wrong password 6 times in a row locks that IP out
  for 5 minutes

### IP-only setup (no domain, self-signed certificate)

Use this instead of steps 3-5 above if you're not using DuckDNS or any
other hostname.

```
# 1. Firewall / EC2 security group: open only 80 and 443, same as above.

# 2. Install nginx
sudo dnf install -y nginx

# 3. Generate a self-signed certificate for your actual public IP
sudo chmod +x deploy/generate-selfsigned-cert.sh
sudo deploy/generate-selfsigned-cert.sh <your-ec2-public-ip>

# 4. Install the IP-only nginx config
sudo cp deploy/nginx-email-agent-ip.conf /etc/nginx/conf.d/email-agent.conf
sudo systemctl enable --now nginx

# 5. Switch the review service to gunicorn (same as step 5 above)
sudo cp deploy/email-agent-review.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart email-agent-review
```
Then visit `https://<your-ec2-public-ip>` -- your browser will warn that
the connection isn't private (expected, because the cert isn't from a
trusted authority). Click through the warning (usually "Advanced" ->
"Proceed") to reach the login page. Anyone else using this will see and
have to click through the same warning every time -- worth telling your
reviewers to expect it up front so it doesn't look like a phishing page.

**Verify the same three things as step 6 above**, substituting the IP for
the domain, plus confirm port 5000 is genuinely unreachable from outside.


**Ongoing, once this is public:**
- Rotate the password periodically -- re-run `deploy/hash_password.py`
  and update `.env`, then restart the service.
- Watch for repeated failed logins: `sudo journalctl -u email-agent-review -f`
  and nginx's access log will both show them.
- **Know the accountability limitation:** everyone who has the password
  logs in as the same shared account, so the audit log can show *that* a
  case was approved but not *which specific person* approved it. That's
  fine for one reviewer; if more than one person will use this, plan to
  move to per-user accounts before relying on the logs for accountability
  in an incident review.
- This is still an internal review tool, not a customer-facing surface --
  don't link it from anywhere public, and treat the URL itself as
  sensitive even though the login page protects what's behind it.

## Multi-user accounts and roles

The review page now supports real, individual accounts instead of one
shared login. Three roles, each including everything the level below it
can do:

- **read** -- can view the queue (original email, classification, draft)
  but cannot approve, reject, or edit anything. For auditors/observers.
- **write** -- can review, edit drafts, and approve/reject. This is the
  reviewer role -- what the old shared login used to do.
- **admin** -- everything write can do, plus creating/managing user
  accounts under **Admin** in the top navigation.

**Nothing breaks on upgrade:** the `REVIEW_USERNAME` / `REVIEW_PASSWORD_HASH`
already in your `.env` automatically become the first admin account the
first time the app runs after this update (stored from then on in
`data/users.json`, not read from `.env` again). Create every other
account through the Admin panel instead of editing `.env`.

**First-run admin password:** leave `REVIEW_PASSWORD_HASH` blank in `.env`
and the app generates a random password automatically, printing it
**once** to the logs:
```
docker compose logs review | grep -A 6 "FIRST-RUN ADMIN"
```
Log in with that, then change it immediately under **My Account**. Only a
one-way hash is ever stored -- if you lose that password before changing
it, there is no way to recover it; delete `data/users.json` and restart
the review container to generate a fresh one. If you'd rather choose the
first-run password yourself instead of a random one, run
`python deploy/hash_password.py` and paste the resulting
`REVIEW_PASSWORD_HASH` into `.env` before first startup.

**Password resets, two ways:**
- Anyone can change their own password under **My Account** (requires
  knowing the current password).
- An admin can reset *any* user's password from the Admin panel without
  needing to know their old one -- for when someone's locked out.

**Safety rails built in:** an admin can't deactivate their own account,
and can't demote themselves away from admin if they're the last active
admin -- both are blocked server-side so a mistake can't lock everyone
out of user management entirely.

**Accountability:** every approved/rejected case in `logs/audit.jsonl`
now records `resolved_by` with the actual username -- the shared-login
gap flagged in the governance deck is closed.

**If running via Docker**, `data/` needs to be bind-mounted for account
data to persist across container rebuilds -- already wired into
`docker-compose.yml`, no action needed if you're on the current version.

## Live agent logs, in the browser

A new **Logs** tab shows the same detailed per-agent trace as `docker
compose logs -f poller` -- classification results, chunk-retrieval
counts, draft generation, guardrail checks, SMTP failures -- without
needing server or SSH access. This is the piece that was missing before:
the Governance tab summarizes the audit trail (business-level outcomes),
while Logs shows the raw, live operational trace underneath it.

Color-coded by level (INFO in muted gray, WARNING in amber, ERROR in
red), most recent first, click Refresh to pull the latest. Both the
poller and review containers write into the same shared, rotating log
file (`logs/agent-activity.log`, capped at 5MB x 3 backups so it can't
grow unbounded) -- this is separate from `logs/audit.jsonl`, which is
the permanent, PII-redacted compliance record and is never trimmed.

**Access note:** unlike the audit log, these lines are not redacted --
they show the same level of detail as the review queue itself (email
addresses, subject lines). The Logs page is available to every role
(read and up), matching the queue's access level, not restricted further.

## Governance dashboard and editable guardrails policy

A new **Governance** tab (visible to every role) shows a live summary
read from `logs/audit.jsonl`: emails sent, rejected, escalated, and
quarantined, an approval rate, the top quarantine reasons, and a recent
activity feed -- the kind of view worth screenshotting for a project
review, since it's reading the real audit trail, not a mockup.

**For admins specifically**, the same page includes an editable
guardrails policy panel:
- The prompt-injection detection patterns (one per line)
- The quarantine length threshold
- Which PII categories get redacted in logs (email / phone / card)

**Changes apply to the very next email, no restart needed** -- both the
poller and review containers read the same policy file
(`data/guardrails_policy.json`, bind-mounted into both). Every save is
itself logged to the audit trail as a `guardrails_policy_updated` event,
so policy changes are traceable the same way case decisions are.

**Safety rails on the editor itself:** every pattern is validated as a
real regex before it's allowed to save -- a typo can't silently break
guardrail checks for every future email. If the saved pattern list is
ever empty, the dashboard shows a clear warning that prompt-injection
detection is currently off, rather than failing silently.

## Observability -- watching what each agent is doing

Every stage now logs to stdout with a timestamp and case ID, which
`docker compose logs` captures automatically -- this is the primary way
to see what's happening and troubleshoot issues.

**Watch the pipeline live:**
```
docker compose logs -f poller
```
A normal email produces a trace like this, one case fully readable start
to finish:
```
2026-09-08 11:30:01 INFO  poller        New email -- from=customer@x.com subject='Where is my order?'
2026-09-08 11:30:01 INFO  orchestrator  [a1b2c3d4] new email -- from=customer@x.com subject='Where is my order?'
2026-09-08 11:30:02 INFO  orchestrator  [a1b2c3d4] classified -- intent=shipping urgency=low requires_human=False
2026-09-08 11:30:03 INFO  orchestrator  [a1b2c3d4] draft generated (412 characters)
2026-09-08 11:30:03 INFO  orchestrator  [a1b2c3d4] waiting for human review
2026-09-08 11:45:20 INFO  orchestrator  [a1b2c3d4] review decision: approved (by jane)
2026-09-08 11:45:21 INFO  orchestrator  [a1b2c3d4] sent successfully to customer@x.com
```
Grep for a specific case across the whole run: `docker compose logs poller | grep a1b2c3d4`

**Watch the review page / admin activity live:**
```
docker compose logs -f review
```
Shows every login attempt (success and failure, with IP), every
approve/reject decision (with who did it), and every admin action (user
created, role changed, password reset, account activated/deactivated) --
useful both for troubleshooting and as a day-to-day security log of who
did what.

**Control the verbosity** with `LOG_LEVEL` in `.env`:
- `INFO` (default) -- normal operation, one line per meaningful step
- `DEBUG` -- adds low-level detail (IMAP connection attempts, guardrail
  pass-through, knowledge-base chunk counts) -- turn this on temporarily
  when actively troubleshooting, then set it back to `INFO`

**This is operational logging, separate from the audit log.**
`logs/audit.jsonl` (written by `app/logger.py`) is the compliance-focused
record -- one structured, PII-redacted JSON line per case, kept
long-term. The logging described here is for day-to-day troubleshooting
and is not redacted the same way -- don't pipe it somewhere with looser
access controls than the audit log has.

## Production: Docker deployment (recommended)

This replaces manually managing Python, `venv`, and two separate systemd
services on the host -- most of the actual incidents so far (Python
version mismatch, file ownership fights, dependency drift, stale service
files, port conflicts) trace back to that manual management. Docker
packages an exact Python version and exact dependencies into one
reproducible image, eliminating that whole category of problem.

**What doesn't change:** Ollama keeps running natively on the host
(already working, no reason to touch it), and nginx's config is
untouched -- both containers use `network_mode: host`, so the review app
still answers on `127.0.0.1:5000` exactly as before.

**1. Install Docker** (Amazon Linux 2023):
```
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker admin
```
Log out and back in (or run `newgrp docker`) for the group change to take
effect. Then confirm the Compose plugin is available:
```
docker compose version
```
If that command isn't found, follow Docker's official Compose plugin
install instructions for your distro -- package availability varies.

**2. Stop and disable the old venv-based services** (don't delete them
yet -- keep as a rollback path until the Docker version is confirmed
working):
```
sudo systemctl stop email-agent-poller email-agent-review
sudo systemctl disable email-agent-poller email-agent-review
```

**3. Confirm ownership** (same habit as before, still matters for the
bind-mounted `knowledge/`, `chroma_db/`, and `logs/` folders):
```
sudo chown -R admin:admin /home/admin/email-agent
```

**4. Build and start the stack:**
```
cd /home/admin/email-agent
docker compose build
docker compose up -d
docker compose ps        # both containers should show "Up"
```

**5. Rebuild the knowledge base index** (runs once inside the container,
writes into the bind-mounted `chroma_db/` on the host):
```
docker compose run --rm poller python build_index.py
```

**6. Install the systemd wrapper** so the whole stack starts on boot and
you can still use `systemctl status`:
```
sudo cp deploy/email-agent-docker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable email-agent-docker
```
(You don't need to `start` it -- `docker compose up -d` in step 4 already
started both containers, and Docker's own restart policy keeps them
running. The systemd unit's job is just making sure the stack comes back
after a server reboot.)

**7. Verify everything still works** exactly as before: login page loads
over HTTPS, a real email drafts and can be approved, an escalated case
shows with the amber banner, and a sent reply actually arrives.

**Day-to-day commands, going forward:**
```
docker compose logs -f poller      # live logs, replaces journalctl for this
docker compose logs -f review
docker compose restart poller      # after editing app code + rebuilding
docker compose build && docker compose up -d   # after any code change
docker compose down                # stop everything
```

**If something goes wrong and you need to roll back:**
```
docker compose down
sudo systemctl enable --now email-agent-poller email-agent-review
```

## Running it manually (for testing / development)

If you're actively developing rather than running unattended, it's often
easier to run both pieces in the foreground so you can see output live and
restart them quickly. This needs **two terminals**, both with the venv
activated.

**Terminal 1 -- start the review page:**
```
python review_app.py
```
Leave this running. Open http://localhost:5000 in a browser -- it starts
empty ("Nothing waiting right now") until a case needs review.

**Terminal 2 -- offline test (no mailbox needed) -- do this first:**
```
python main.py --mode test
```
It will print `waiting for review -- open http://localhost:5000` and pause.
Go to the browser tab: you'll see the original email, the classification,
and an editable textarea with the drafted reply. Edit it if you want, then
click **Approve & send** or **Reject**. Terminal 2 resumes immediately and
finishes the case. Check `logs/audit.jsonl` afterward -- that's your audit
trail, and it records whichever draft you actually approved (edited or not).

**Real inbox test (with DRY_RUN=true), also in Terminal 2:**
```
python main.py --mode inbox
```
Point `.env` at a throwaway test mailbox first, not a live one. Send
yourself a few realistic test emails -- an order question, a complaint, and
one deliberately trying prompt injection ("ignore your instructions and...")
-- and confirm the complaint and injection attempt both get escalated or
quarantined rather than auto-drafted.

## Before you go live (DRY_RUN=false, real mailbox)

- [ ] **Run this past your project lead and AI governance review first** --
      use the companion governance briefing to confirm mailbox scope and
      EU AI Act classification before anything goes live.
- [ ] **Move secrets out of `.env`** into a proper secrets manager if this
      leaves your laptop (e.g. a shared server or cloud VM).
- [ ] **Replace the regex-based guardrails** in `app/guardrails.py` with a
      maintained PII-detection library (e.g. Microsoft Presidio) and a
      reviewed prompt-injection defense -- the patterns here are a
      starting point, not a control to rely on long-term.
- [ ] **Add rate limiting and a recipient allowlist** to `app/sender.py` so
      a single bad case can't send more than one email, or fan out to
      unintended recipients.
- [ ] **Keep the human review gate on** (`app/review.py`) until the system
      has a real track record. Removing it is a governance decision, made
      deliberately -- not something to do by skipping the prompt.
- [ ] **Put `review_app.py` behind real authentication** -- done: see
      "Exposing the review page publicly" above (login + TLS + gunicorn +
      nginx). If more than one reviewer will use it, move from the shared
      login to per-user accounts before relying on the audit log for
      individual accountability.
- [ ] **If running unattended via systemd without public exposure**,
      restrict access via an SSH tunnel to whoever is authorized to
      approve replies, rather than opening it up.
- [ ] **Automate re-indexing** (`build_index.py`) so the knowledge base
      stays current as policies change, instead of relying on someone to
      remember.
- [ ] **Set `DRY_RUN=false`** only after all of the above, and only against
      a mailbox your team has explicitly approved.

## Project structure

```
email-agent/
  Dockerfile             # builds the app image (Python 3.11, pinned)
  docker-compose.yml      # defines the poller + review containers
  .dockerignore
  .env.example        # copy to .env and fill in
  requirements.txt
  build_index.py       # indexes knowledge/ into the vector database
  main.py               # entry point: --mode test | inbox
  knowledge/            # your policy/FAQ text files (edit these)
  app/
    config.py           # loads .env
    guardrails.py        # input sanitization, PII redaction
    classifier.py        # labels intent, urgency, requires_human
    retriever.py          # searches the knowledge base
    drafter.py             # writes the reply (company facts + general knowledge)
    review.py               # human approval gate (waits on the web page)
    review_store.py          # shared pending-case store used by review.py + review_app.py
    user_store.py             # user accounts, roles, password hashes
    auth.py                    # role-aware login_required decorator
    policy_store.py             # editable guardrails policy (patterns, PII toggles)
    audit_reader.py              # summarizes logs/audit.jsonl for the governance dashboard
    log_reader.py                 # reads logs/agent-activity.log for the Logs page
    logging_setup.py               # stdout + shared file logging for all agents
    sender.py                     # sends the approved reply (or dry-runs it)
    logger.py                      # append-only audit log
    orchestrator.py                 # wires every agent together
  review_app.py         # Flask review page: multi-user, roles, admin panel, governance dashboard
  deploy/               # systemd units, nginx config, password-hash helper
    email-agent-docker.service      # current -- wraps the Docker Compose stack
    email-agent-review.service      # legacy -- venv/gunicorn rollback path
    email-agent-poller.service      # legacy -- venv rollback path
    nginx-email-agent.conf          # for a domain (DuckDNS or real) + Let's Encrypt
    nginx-email-agent-ip.conf       # for a bare public IP + self-signed cert
    generate-selfsigned-cert.sh
    hash_password.py
  data/                 # users.json -- account data (Docker: bind-mounted)
  logs/                # audit.jsonl + pending_reviews.json written here
  chroma_db/           # vector database files, generated by build_index.py
```
