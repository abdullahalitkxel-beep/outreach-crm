"""Pages for the BSO Bot's control room, the supervisor's workspace and the bot's settings."""

ROLE_TEMPLATES = {}

ROLE_TEMPLATES["botroom.html"] = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Status{% endblock %}{% block body %}
{% call m.header('t-home', '●', 'BSO Bot', 'Running' if not rp.killed else 'Stopped') %}
<form method="post" action="{{ url_for('bot_kill_local') }}" style="margin:0" onsubmit="return confirm('{{ 'Start sending again?' if rp.killed else 'Stop ALL sending for every client?' }}')">
{% if rp.killed %}<input type="hidden" name="on" value="0"><button class="btn brand">▶ Start sending</button>{% else %}<input type="hidden" name="on" value="1"><button class="btn" style="background:#c23934;color:#fff">■ Stop all sending</button>{% endif %}</form>{% endcall %}
{% if rp.killed %}<div class="help" style="border-color:#c23934">{{ rp.killed_why }}</div>{% endif %}
{% if not active_on %}<div class="help">The bot rules (arrival windows, limits by reputation, quality check) are off on this computer. They switch on by themselves on the BSO Bot account.</div>{% endif %}
<div class="kpis">
 <div class="kpi"><small>Sent today</small><b>{{ sent }}</b><span class="muted">of {{ cap }} today's capacity</span></div>
 <div class="kpi"><small>Inboxes sending</small><b>{{ rp.inboxes|selectattr('limit')|list|length }}</b><span class="muted">of {{ rp.inboxes|length }}</span></div>
 <div class="kpi"><small>Waiting in Review</small><b>{{ review_count or 0 }}</b></div>
 <div class="kpi"><small>Last run</small><b style="font-size:16px">{{ (last.finished_at or last.started_at or '')[:16].replace('T', ' ') if last else 'never' }}</b><span class="muted">{{ last.status if last }}</span></div>
</div>
{% if rp.breakers %}<div class="card" style="border-color:#c23934"><div class="card-h"><h2>Paused by the safety breaker</h2><span class="muted" style="margin-left:8px">too many bounces, complaints or stop requests; resuming is a person's decision</span></div><table class="tbl">
{% for k in rp.breakers %}<tr><td>{{ {'campaign': 'Campaign', 'list': 'Imported list', 'restore': 'After a restore'}.get(k.kind, k.kind) }} <b>{{ k.key }}</b></td><td class="muted">{{ k.at[:16].replace('T', ' ') }}</td><td>{{ k.reason }}</td>
<td><form method="post" action="{{ url_for('bot_breaker_resume', bid=k.id) }}" style="margin:0" onsubmit="return confirm('Resume? Fix the cause first: clean the list, check the email copy.')"><button class="btn sm">Resume</button></form></td></tr>{% endfor %}</table></div>{% endif %}
<div class="card"><div class="card-h"><h2>Why not more emails today?</h2></div><div class="card-b">{% for k, w in rp.why_not %}<div>• {{ w }}</div>{% else %}<div class="muted">Nothing is holding sending back.</div>{% endfor %}</div></div>
<div class="card"><div class="card-h"><h2>Campaigns</h2></div><table class="tbl"><tr><th>Campaign</th><th>Client</th><th>Autonomy</th><th class="r">Sent today</th><th class="r">Leads waiting</th><th class="r">New / day</th><th class="r">Days of stock</th></tr>
{% for c in rp.campaigns %}<tr><td><a href="{{ url_for('campaign', cid=c.id) }}"><b>{{ c.name }}</b></a><br><span class="muted">{{ c.code }} · {{ c.status }}</span>{% if c.suggest %}<br><span class="badge b-warn">{{ c.suggest }}</span>{% endif %}</td><td>{{ c.client or '–' }}</td><td>{{ c.autonomy }}</td><td class="r">{{ c.sent_today }}</td>
<td class="r">{{ c.stock.ready if c.stock else '' }}</td><td class="r">{{ c.stock.new_per_day if c.stock else '' }}</td><td class="r">{% if c.stock %}{{ c.stock.days if c.stock.days is not none else 'no capacity' }}{% endif %}</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Queue: next first emails</h2><span class="muted" style="margin-left:8px">they land in each lead's own morning</span></div><table class="tbl">{% for q in rp.queue %}<tr><td>{{ q.email }}</td><td>{{ q.campaign }}</td><td class="muted">{{ q.next_send_at[:16].replace('T', ' ') }} UTC</td></tr>{% else %}<tr><td class="muted">Nothing queued.</td></tr>{% endfor %}</table></div>
{% if rp.spot_checks %}<div class="card"><div class="card-h"><h2>Spot checks</h2><span class="muted" style="margin-left:8px">emails the bot sent on its own, for a person to look at</span></div>
{% for s in rp.spot_checks %}<div class="card-b" style="border-top:1px solid #eee"><b>{{ s.lead_email }}</b> <span class="muted">from {{ s.sender }} · quality {{ s.score }}</span><br><b>{{ s.subject }}</b><pre style="white-space:pre-wrap;font:inherit">{{ s.body }}</pre>
<form method="post" action="{{ url_for('bot_spot', sid=s.id) }}" style="display:flex;gap:6px"><input name="note" placeholder="Note (optional)"><button class="btn sm" name="status" value="ok">Looks fine</button><button class="btn sm" name="status" value="problem">Problem</button></form></div>{% endfor %}</div>{% endif %}
<div class="card"><div class="card-h"><h2>Backups</h2></div><div class="card-b">{% set bk = rp.backup or {} %}
{% if bk %}<div>Last backup {{ bk.at[:16].replace('T', ' ') }} UTC: <span class="badge {{ 'b-ok' if bk.ok else 'b-bad' }}">{{ 'restore test passed' if bk.ok else 'problem' }}</span> <span class="muted">{{ bk.detail }}</span></div>
<div>Google Drive copy: {% if bk.drive_ok %}<span class="badge b-ok">saved and checked</span> <span class="muted">{{ bk.drive }}</span>{% elif bk.drive_detail %}<span class="badge b-bad">problem</span> {{ bk.drive_detail }}{% elif drive_ready %}<span class="muted">made with the next backup</span>{% else %}<span class="badge b-warn">not available</span> <span class="muted">needs the Google script updated to version 6 (Master Console → Settings)</span>{% endif %}</div>
{% if bk.offsite_ok or bk.offsite_detail or backup_folder %}<div>Backup folder copy: {% if bk.offsite_ok %}<span class="badge b-ok">saved</span> <span class="muted">{{ bk.offsite }}</span>{% elif bk.offsite_detail %}<span class="badge b-bad">problem</span> {{ bk.offsite_detail }}{% else %}<span class="muted">made with the next backup</span>{% endif %}</div>{% endif %}
{% else %}<div class="muted">The first backup is made at the next run (then once a day).</div>{% endif %}
<form method="post" action="{{ url_for('save_backup') }}" style="margin-top:8px"><button class="btn sm" name="now" value="1">Back up now</button></form>
<p class="muted" style="margin:6px 0 0">Every 6 hours a sealed copy goes to the owner's Google Drive (folder "BSO Team Data/backups"), is read back and checked; the newest 14 are kept. It is sealed with the team key, so neither Google nor anyone opening the file can read it.</p>
<details style="margin-top:6px"><summary class="muted">Also copy to a folder (optional)</summary><form method="post" action="{{ url_for('save_backup') }}" style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap"><input name="folder" value="{{ backup_folder }}" placeholder="e.g. D:\BSO backups (leave empty for none)" style="flex:1;min-width:260px"><button class="btn sm">Save folder</button></form></details>
{% if can_restore %}<details style="margin-top:8px" {{ 'open' if drive is not none or drive_err }}><summary>Restore a backup</summary>
{% if drive_ready %}{% if drive is none %}<a class="btn sm" href="{{ url_for('botroom', drive=1) }}" style="margin-top:6px">Show backups on Google Drive</a>{% if drive_err %} <span class="badge b-bad">problem</span> {{ drive_err }}{% endif %}
{% else %}<form method="post" action="{{ url_for('save_restore') }}" style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap"><select name="drive_name" style="flex:1;min-width:260px">{% for b in drive if b.complete %}<option value="{{ b.name }}">{{ b.name[11:15] }}-{{ b.name[15:17] }}-{{ b.name[17:19] }} {{ b.name[20:22] }}:{{ b.name[22:24] }} ({{ '%.1f'|format(b.size / 1000000) }} MB)</option>{% else %}<option value="">No backups on Google Drive yet</option>{% endfor %}</select>
<input name="confirm" placeholder="Type RESTORE" style="width:130px" required><button class="btn sm danger">Restore from Google Drive</button></form>{% endif %}{% endif %}
<form method="post" action="{{ url_for('save_restore') }}" style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap">
<input name="path" placeholder="Or the full path of a .bsobak or .db backup file" style="flex:1;min-width:260px" required><input name="confirm" placeholder="Type RESTORE" style="width:130px" required><button class="btn sm danger">Restore file</button></form>
<p class="muted" style="margin:6px 0 0">The current database is kept aside first. Use this on a replacement laptop (see the dead-laptop runbook).</p></details>{% endif %}
</div></div>
<div class="card"><div class="card-h"><h2>Alerts</h2></div><table class="tbl">{% for a in rp.alerts %}<tr><td class="muted">{{ a.at[:16] }}</td><td>{{ a.text }}</td></tr>{% else %}<tr><td class="muted">No alerts sent.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

ROLE_TEMPLATES["botdomains.html"] = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Domains & inboxes{% endblock %}{% block body %}
{% call m.header('t-setup', '@', 'BSO Bot', 'Domains & inboxes') %}{% endcall %}
{% if msg2 %}<div class="toast">{{ msg2 }}</div>{% endif %}{% if err2 %}<div class="toast err">{{ err2 }}</div>{% endif %}
<div class="card"><div class="card-h"><h2>Inboxes</h2></div><table class="tbl"><tr><th>Inbox</th><th>Health</th><th class="r">Today's limit</th><th class="r">Sent today</th><th>Why</th><th>Spam test</th><th></th></tr>
{% for i in rp.inboxes %}<tr><td>{{ i.email }}</td><td><span class="badge {{ 'b-ok' if i.rating == 'healthy' else '' }}">{{ 'paused' if i.paused else i.rating or ('ready' if i.ready else 'not checked') }}</span></td><td class="r">{{ i.limit }}</td><td class="r">{{ i.sent_today }}</td>
<td class="muted">{{ i.paused or (i.why if i.ready else i.ready_detail) }}</td><td class="muted">{{ i.seed }}</td>
<td><form method="post" style="margin:0"><input type="hidden" name="action" value="readiness"><input type="hidden" name="email" value="{{ i.email }}"><button class="btn sm">Check now</button></form></td></tr>
{% else %}<tr><td colspan="7" class="muted">No inboxes yet: your manager adds them in the Master Console → BSO setup.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Clients and their sending domains</h2></div><div class="card-b"><div class="grid2">
<div><table class="tbl"><tr><th>Client</th><th>Line of business</th></tr>{% for c in rp.clients %}<tr><td>{{ c.name }}</td><td>{{ c.line or '–' }}</td></tr>{% endfor %}</table>
<form method="post"><input type="hidden" name="action" value="client"><label>Add a client</label><input name="name" placeholder="Client name" required><input name="line" placeholder="Line of business, e.g. tech services"><button class="btn" style="margin-top:6px">Add client</button></form></div>
<div><table class="tbl"><tr><th>Domain</th><th>Client</th><th class="r">Inboxes</th><th class="r">Sent today</th></tr>{% for d in rp.domains %}<tr><td>{{ d.domain }}</td><td>{{ d.client }}</td><td class="r">{{ d.inboxes|length }}</td><td class="r">{{ d.sent_today }}{{ ' / ' ~ d.cap if d.cap }}</td></tr>{% endfor %}</table>
<form method="post"><input type="hidden" name="action" value="domain"><label>Add a sending domain</label><input name="domain" placeholder="getneosofttech.com" required><select name="client">{% for c in rp.clients %}<option>{{ c.name }}</option>{% endfor %}</select><input name="cap" type="number" min="0" placeholder="Daily cap (0 = sum of its inboxes)"><button class="btn" style="margin-top:6px">Add domain</button></form>
<p class="hint">A domain belongs to one client only and is never shared with another. Every inbox under it sends for the campaigns that choose it.</p></div></div></div></div>
{% for c in camps %}<div class="card"><div class="card-h"><h2>{{ c.name }}</h2><span class="muted" style="margin-left:8px">{{ c.code }}</span></div><div class="card-b"><form method="post"><input type="hidden" name="action" value="campaign"><input type="hidden" name="cid" value="{{ c.id }}"><div class="grid2">
<div><label>Client</label><select name="client"><option value="">–</option>{% for cl in rp.clients %}<option {{ 'selected' if cl.name == c.client }}>{{ cl.name }}</option>{% endfor %}</select></div>
<div><label>Sending domains</label>{% for d in rp.domains %}{% if not c.client or d.client == c.client %}<label class="check"><input type="checkbox" name="domains" value="{{ d.domain }}" {{ 'checked' if d.domain in c.domains }}> {{ d.domain }} ({{ d.inboxes|length }} inboxes)</label>{% endif %}{% else %}<span class="muted">Add a domain first.</span>{% endfor %}</div>
<div><label>Share when a domain serves several campaigns (weight)</label><input name="weight" type="number" min="1" value="{{ c.weight or 1 }}"></div>
<div><label>Autonomy</label><select name="autonomy">{% for a, l in [('assisted','Assisted: every email waits for approval'),('supervised','Supervised: high-confidence emails go alone'),('autonomous','Autonomous: everything that passes the check goes')] %}<option value="{{ a }}" {{ 'selected' if a == (c.autonomy or 'assisted') }}>{{ l }}</option>{% endfor %}</select></div>
<div><label>First-email arrival window (lead's own time)</label><input type="time" name="arrive_from" value="{{ c.window.split('-')[0] }}" style="width:45%"> to <input type="time" name="arrive_to" value="{{ c.window.split('-')[1] }}" style="width:45%"></div>
<div><label>Answers to information requests</label><select name="info_mode">{% for k, l in [('off','Off'),('prompt','AI prompt'),('template','Template')] %}<option value="{{ k }}" {{ 'selected' if k == (c.info_mode or 'off') }}>{{ l }}</option>{% endfor %}</select></div>
<div style="grid-column:1/-1"><label>Prompt or template for those answers</label><textarea name="info_text" style="min-height:90px">{{ c.info_text or '' }}</textarea></div></div>
<button class="btn brand" style="margin-top:8px">Save</button></form></div></div>{% endfor %}
{% endblock %}"""

ROLE_TEMPLATES["botintake.html"] = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Lead intake{% endblock %}{% block body %}
{% call m.header('t-leads', '⇩', 'BSO Bot', 'Lead intake') %}<a class="btn" href="{{ url_for('bot_template') }}">⬇ Lead template</a>{% endcall %}
<div class="card"><div class="card-h"><h2>Lead stock</h2></div><table class="tbl"><tr><th>Campaign</th><th>Code</th><th class="r">Leads waiting</th><th class="r">New a day</th><th class="r">Days of stock</th></tr>
{% for c in stock %}<tr><td>{{ c.name }}</td><td>{{ c.code }}</td><td class="r">{{ c.stock.ready }}</td><td class="r">{{ c.stock.new_per_day }}</td><td class="r">{{ c.stock.days if c.stock.days is not none else 'no capacity' }}</td></tr>{% endfor %}</table>
<div class="card-b hint">Days of stock count follow-ups first: each new lead costs about {{ stock[0].stock.cost_per_lead if stock else 3.4 }} emails over its sequence. Below 10 days the owner gets an email with the template attached, again every 2 days, urgent below 3.</div></div>
<div class="card"><div class="card-h"><h2>Lead intake mailbox</h2>{% if b.email and pw %}<span class="badge b-ok">connected: {{ b.email }}</span>{% endif %}</div><div class="card-b">
<p class="muted" style="margin-top:0">A mailbox used only for lead files, e.g. leads@blacswanken.com. Only the owner's address is accepted, and only when the mail server confirms it. Alerts and the daily digest are sent from here too.</p>
<form method="post"><div class="grid2">
<div><label>Intake mailbox address</label><input name="email" type="email" value="{{ b.email or '' }}" placeholder="leads@blacswanken.com" required></div>
<div><label>Accepted sender (the owner)</label><input name="owner" type="email" value="{{ b.owner or 'omar@blacswanken.com' }}"></div>
<div><label>Password / app password {% if pw %}<span class="badge b-ok">saved</span>{% endif %}</label><input name="password" type="password" autocomplete="new-password" placeholder="{{ 'Leave blank to keep the saved one' if pw else 'App password' }}"></div>
<div><label>Name on alerts</label><input name="name" value="{{ b.name or 'BSO Bot' }}"></div>
<div><label>Receiving server (IMAP)</label><input name="imap_host" value="{{ b.imap_host or 'imap.gmail.com' }}"></div><div><label>IMAP port</label><input name="imap_port" type="number" value="{{ b.imap_port or 993 }}"></div>
<div><label>Sending server (SMTP)</label><input name="smtp_host" value="{{ b.smtp_host or 'smtp.gmail.com' }}"></div><div><label>SMTP port</label><input name="smtp_port" type="number" value="{{ b.smtp_port or 465 }}"></div></div>
<label class="check"><input type="checkbox" name="verified_only" {{ 'checked' if b.get('verified_only', True) }}> Only accept files the mail server confirms (SPF/DKIM/DMARC pass)</label>
<button class="btn brand" style="margin-top:8px">Save &amp; check now</button></form>
<p class="hint">Email <b>STOP ALL</b> (in the subject) to this mailbox from the owner's address to stop all sending at once; <b>START ALL</b> starts it again.</p></div></div>
<div class="card"><div class="card-h"><h2>Received</h2></div><table class="tbl"><tr><th>When</th><th>From</th><th>Subject</th><th class="r">Added</th><th class="r">Rejected</th><th>Detail</th></tr>
{% for r in rows %}<tr><td class="muted">{{ r.at[:16] }}</td><td>{{ r.frm }}</td><td>{{ r.subject }}</td><td class="r">{{ r.added }}</td><td class="r">{{ r.rejected }}</td><td class="muted" style="white-space:pre-wrap">{{ r.detail }}</td></tr>{% else %}<tr><td colspan="6" class="muted">Nothing received yet.</td></tr>{% endfor %}</table></div>
<div class="card"><div class="card-h"><h2>Template columns</h2></div><table class="tbl"><tr><th>Column</th><th>Required</th><th>Example</th><th>Notes</th></tr>{% for c, r, ex, n in cols %}<tr><td>{{ c }}</td><td>{{ 'Yes' if r else 'No' }}</td><td class="muted">{{ ex }}</td><td class="muted">{{ n }}</td></tr>{% endfor %}</table></div>
{% endblock %}"""

ROLE_TEMPLATES["botactivity.html"] = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}Activity{% endblock %}{% block body %}
{% call m.header('t-rep', '≡', 'BSO Bot', 'Activity') %}<a class="btn" href="{{ url_for('jobs') }}">Runs</a>{% endcall %}
<div class="card"><div class="card-h"><h2>Every automatic decision, and why</h2></div><table class="tbl">{% for a in items %}<tr><td class="muted" style="white-space:nowrap">{{ a.at[:16].replace('T', ' ') }}</td><td>{{ a.kind }}</td><td>{% if a.lead_id %}<a href="{{ url_for('lead', lead_id=a.lead_id) }}">{{ a.detail }}</a>{% else %}{{ a.detail }}{% endif %}</td></tr>{% else %}<tr><td class="muted">Nothing yet.</td></tr>{% endfor %}</table></div>
{% endblock %}"""

ROLE_TEMPLATES["unlock.html"] = r"""{% extends 'base.html' %}{% block title %}Settings locked{% endblock %}{% block body %}
<div class="card" style="max-width:460px;margin:40px auto"><div class="card-h"><h2>Settings are locked</h2></div><div class="card-b">
<p>On the BSO Bot, settings open with the bot's password, so nobody passing the laptop can change limits or inboxes. Most changes can also be made from the Master Console.</p>
{% if err %}<div class="toast err">{{ err }}</div>{% endif %}<form method="post"><input type="hidden" name="next" value="{{ nxt }}"><label>The bot's password</label><input name="password" type="password" autofocus required><button class="btn brand" style="margin-top:8px">Unlock for 15 minutes</button></form></div></div>{% endblock %}"""

ROLE_TEMPLATES["myday.html"] = r"""{% extends 'base.html' %}{% import 'macros.html' as m %}{% block title %}My day{% endblock %}{% block body %}
{% call m.header('t-home', '☀', 'My day', team_name or 'Today') %}{% endcall %}
<div class="card"><div class="card-h"><h2>Hot and warm leads waiting for you</h2></div><table class="tbl">
{% for h in hot %}<tr><td>{{ temp_badge(h.temperature) }}</td><td><a href="{{ url_for('lead', lead_id=h.lead_id) }}"><b>{{ h.first_name or h.email }}</b></a> <span class="muted">{{ h.company }}</span><br><span class="muted">{{ h.subject }}</span></td>
<td class="r">{% if h.left is not none %}{% if h.left < 0 %}<span class="badge" style="background:#fde1e1;color:#8c1c13">{{ (-h.left) // 60 }}h {{ (-h.left) % 60 }}m overdue</span>{% else %}<span class="badge b-warn">{{ h.left // 60 }}h {{ h.left % 60 }}m left</span>{% endif %}{% endif %}</td></tr>
{% else %}<tr><td class="muted">Nobody is waiting for a reply from you.</td></tr>{% endfor %}</table></div>
{% if new_prospects %}<div class="card"><div class="card-h"><h2>New prospects handed to you</h2></div><table class="tbl">{% for p in new_prospects %}<tr><td><a href="{{ url_for('lead', lead_id=p.id) }}"><b>{{ p.first_name or p.email }}</b></a> <span class="muted">{{ p.company }}</span></td><td>{{ temp_badge(p.temperature) }}</td></tr>{% endfor %}</table></div>{% endif %}
{% if unread %}<div class="card"><div class="card-h"><h2>Unread replies</h2></div><table class="tbl">{% for u in unread %}<tr><td><a href="{{ url_for('lead', lead_id=u.lead_id) }}"><b>{{ u.first_name or u.frm }}</b></a> <span class="muted">{{ u.company }}</span><br>{{ u.subject }}</td><td class="muted">{{ u.at[:16] }}</td></tr>{% endfor %}</table></div>{% endif %}
<div class="card"><div class="card-h"><h2>Tasks</h2></div><table class="tbl">
{% for t in overdue %}<tr><td><span class="badge" style="background:#fde1e1;color:#8c1c13">overdue {{ t.due_date }}</span></td><td>{{ t.subject }}</td><td>{% if t.lead_id %}<a href="{{ url_for('lead', lead_id=t.lead_id) }}">{{ t.first_name or t.email }}</a>{% endif %}</td></tr>{% endfor %}
{% for t in today_tasks %}<tr><td><span class="badge b-warn">today</span></td><td>{{ t.subject }}</td><td>{% if t.lead_id %}<a href="{{ url_for('lead', lead_id=t.lead_id) }}">{{ t.first_name or t.email }}</a>{% endif %}</td></tr>{% endfor %}
{% if not overdue and not today_tasks %}<tr><td class="muted">Nothing due today.</td></tr>{% endif %}</table></div>
{% endblock %}"""

SETTINGS_EXTRA = r"""
{% elif tab == 'autopilot' %}{% set ap = c.get('autopilot') or {} %}
<div class="card"><div class="card-h"><h2>Bot rules</h2></div><div class="card-b"><form method="post" action="{{ url_for('save_autopilot') }}">
{% if role_mode == 'full' %}<label class="check"><input type="checkbox" name="enabled" {{ 'checked' if ap.get('enabled') }}> Use the bot rules on this computer (on the BSO Bot account they're always on)</label>{% endif %}
<div class="grid2">
<div><label>Owner (alerts, digest, accepted lead files)</label><input name="owner" type="email" value="{{ ap.get('owner') or 'omar@blacswanken.com' }}"></div>
<div><label>Default first-email arrival window (lead's own time)</label><input type="time" name="window_from" value="{{ (ap.get('window') or '08:45-10:30').split('-')[0] }}" style="width:45%"> to <input type="time" name="window_to" value="{{ (ap.get('window') or '08:45-10:30').split('-')[1] }}" style="width:45%"></div>
<div><label>Daily limit per inbox: floor</label><input name="floor" type="number" min="1" value="{{ ap.get('floor') or 5 }}"></div>
<div><label>Daily limit per inbox: ceiling (the bot never goes above it)</label><input name="ceiling" type="number" min="1" value="{{ ap.get('ceiling') or 30 }}"></div>
<div><label>Starting limit for a new warmed-up inbox (half of it for the first 5 working days)</label><input name="start_limit" type="number" min="1" value="{{ ap.get('start_limit') or 10 }}"></div>
<div><label>Emails to one recipient domain per day</label><input name="recipient_domain_per_day" type="number" min="1" value="{{ ap.get('recipient_domain_per_day') or 2 }}"></div>
<div><label>People at one company per 30 days</label><input name="company_people_30d" type="number" min="1" value="{{ ap.get('company_people_30d') or 3 }}"></div>
<div><label>Days before the same person can be emailed for another client in the same line of business</label><input name="cross_client_days" type="number" min="0" value="{{ ap.get('cross_client_days') or 90 }}"></div>
<div><label>Minimum days between two first emails to one person (any client)</label><input name="first_email_gap_days" type="number" min="0" value="{{ ap.get('first_email_gap_days') or 7 }}"></div>
<div><label>Low lead stock alert below (working days)</label><input name="stock_alert_days" type="number" min="1" value="{{ ap.get('stock_alert_days') or 10 }}"></div>
<div><label>Alert hour (Pakistan time)</label><input name="alert_hour" type="number" min="0" max="23" value="{{ ap.get('alert_hour') or 6 }}"></div>
<div><label>Daily digest hour (Pakistan time)</label><input name="digest_hour" type="number" min="0" max="23" value="{{ ap.get('digest_hour') or 8 }}"></div></div>
<h3>Spam-placement test addresses</h3><p class="hint" style="margin-top:0">Each inbox sends these one test email a week; the bot signs in to see whether it landed in the inbox or in spam. Gmail needs an app password; Outlook/Hotmail accounts can't be read with a password any more (Microsoft sign-in is not supported yet), so those tests are skipped.</p>
<div class="grid2">{% set sd = c.get('seeds') or [] %}{% for i in [1, 2] %}{% set x = sd[i-1] if sd|length >= i else {} %}
<div><label>Test address {{ i }}</label><input name="seed{{ i }}" type="email" value="{{ x.email or '' }}" placeholder="{{ 'you@gmail.com' if i == 1 else 'you@hotmail.com' }}"></div><div><label>Its app password</label><input name="seed{{ i }}_pw" type="password" autocomplete="new-password" placeholder="Leave blank to keep the saved one"></div>{% endfor %}</div>
<button class="btn brand" style="margin-top:10px">Save</button></form></div></div>
{% elif tab == 'updates' %}
<div class="card"><div class="card-h"><h2>About &amp; updates</h2></div><div class="card-b">
<p style="margin-top:0">This is BSO <b>{{ app_version }}</b>.{% if update and update.available %} Version <b>{{ update.latest }}</b> is available.{% elif update %} You have the latest version.{% endif %}</p>
{% if update and update.notes %}<h3>What's new</h3><pre style="white-space:pre-wrap;font:inherit">{{ update.notes }}</pre>{% endif %}
{% if update and update.message %}<p class="hint">{{ update.message }}</p>{% endif %}
<form method="post" action="{{ url_for('updates_page') }}" style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn" name="action" value="check">Check now</button>
{% if update and update.available and update.allowed %}<button class="btn brand" name="action" value="now" onclick="return confirm('BSO closes for about a minute and opens again on the new version.')">Update now</button><button class="btn" name="action" value="tonight">Update tonight</button>{% endif %}</form>
<p class="hint">BSO checks for a new version every 6 hours and only installs releases signed by BlacSwanken's build system. Your data is backed up first, and if the new version doesn't start, BSO goes back to this one. {% if role_mode == 'bot' %}The bot updates on its own 24 hours after a release, at a moment when no email is due.{% else %}If you don't update, BSO does it by itself after 3 days at a quiet moment.{% endif %}</p></div></div>
"""
