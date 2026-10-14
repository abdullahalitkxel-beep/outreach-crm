# NeoSOFT Outreach Bot + CRM

Finds leads, writes personalised cold emails, waits for your approval, sends them,
follows up, detects replies, and tracks everything in a Salesforce-style CRM.

**Safety first:** it ships with **test mode** and **review mode** switched on. Nothing is
emailed until you approve it, and while test mode is on, every email goes to *your own*
inbox instead of the lead.

---

## Windows app (easiest)

1. Download **OutreachCRM.exe** and put it anywhere (Desktop is fine).
2. Double-click it. Windows may say **"Windows protected your PC"** because the app isn't
   code-signed. Click **More info → Run anyway** (only needed the first time).
3. The app opens in its own window with a guided tour. Then open **Getting Started**:
   everything is set up through forms in **Settings**, with no files to edit and nothing to install.

- Your data, settings and keys are stored in `%APPDATA%\OutreachCRM` (paste that into the
  File Explorer address bar to open it). Back up `outreach.db` there now and then.
- Closing the window doesn't stop the app; that way automatic runs keep going. To stop it,
  use **? → Quit the app**. Double-clicking the .exe again just reopens the window.
- New version? Quit the app, replace the .exe, and open it again. Your data is kept.

## Run from source (Mac/Linux, or if you prefer)

1. **Install Python 3.10 or newer** from [python.org/downloads](https://www.python.org/downloads/).
   On Windows, tick **"Add Python to PATH"** during install.
2. **Unzip** this folder somewhere easy, like your Desktop.
3. **Start it:**
   - **Windows:** double-click `start.bat`
   - **Mac:** double-click `start.command` (the first time, right-click → Open)
4. Your browser opens the CRM at `http://127.0.0.1:5000`. A short **guided tour** runs first.
5. Click **Getting Started** in the top bar and follow the checklist. It checks your setup
   automatically and has a button for every step.

Keep the black window open while you use the CRM. Closing it stops the CRM.

---

## What the Getting Started checklist walks you through

| Step | What you do | Where |
|---|---|---|
| 1. Your details | Your inbox address, the name prospects see, mail server, and your test address | `config.yaml` (any text editor) |
| 2. Secret keys | Anthropic API key and inbox app password (Apollo key optional) | `.env` (created for you on first start) |
| 3. Add leads | Import a spreadsheet, or preview and pull leads from Apollo | CRM → Leads → Import |
| 4. Draft emails | Click **Run bot now**; drafts appear in Review | CRM |
| 5. Review | Edit, approve or reject each draft | CRM → Review |
| 6. Send a test | Click **Send approved emails**; check your own inbox | CRM |
| 7. Test replies | Reply to a test email and click **Check replies** | CRM |
| 8. Go live | Turn test mode off once your inboxes are warmed up | `config.yaml` |

After editing `.env`, restart the CRM (close the window and double-click start again).

---

## The CRM at a glance

- **Home**: tasks due, hot replies, drafts waiting for review, the last bot run, recent activity.
- **Leads**: list or kanban view; filter by stage, email status, fit grade or source; sort any
  column; bulk-change stage or pause emails.
  - **Fit grade** comes from the job title: **A** = technical decision maker (CTO, VP Eng, Head of
    Product), **B** = executive or founder, **C** = outside your target roles.
- **Lead page**: a stage path (New → Contacted → Replied → Qualified → Converted), editable details,
  emails sent, notes, logged calls and meetings, tasks, and a full activity timeline.
  **Convert** turns an interested lead into an **Account** and an **Opportunity**.
- **Review**: every email waits here until you approve it. Edit freely, or ask for a new AI opener.
- **Opportunities**: deal kanban (Discovery → Proposal → Negotiation → Closed Won/Lost) with amounts,
  close dates and a weighted forecast.
- **Accounts**: companies, with their contacts and deals.
- **Tasks**: your to-do list. A task is added automatically whenever someone replies with interest.
- **Reports**: lead funnel, reply rate per email step, lead source performance, pipeline forecast.
- **Search** (top bar): any lead, account or opportunity.
- **? menu** (top right): replay the tour, re-show help tips, open the checklist.

---

## How the automation behaves

- **Personalisation:** for each lead, the AI reads their website and profile and writes one opening
  line. If there isn't anything specific to say, it uses your fallback line instead of inventing facts.
- **Follow-ups** go as replies in the same thread after the delays in `config.yaml` (3 and 5 days).
- **Replies** are checked on every run. Anyone who replies leaves the sequence, and a task is created.
  Unsubscribes and bounces go on a permanent do-not-contact list.
- **Sending** happens only Mon–Fri, 9:00–17:00 US Eastern (configurable), under each inbox's daily
  limit, with 1–3 minutes between emails. In test mode the window is ignored so you can test any time.
- **Imports** accept Excel or CSV and match column names automatically. Shared inboxes (info@,
  hello@, help@ …), duplicates and do-not-contact addresses are skipped.

## Apollo (optional)

Add `APOLLO_API_KEY` to `.env`. Edit the searches under `apollo:` in `config.yaml`
(titles, seniorities, regions, company sizes, industries).

- **Preview** (free): shows how many people each search matches, with samples. Any search
  showing 0 has an industry name Apollo doesn't recognise, so fix it.
- **Find leads** (about 1 credit per lead): only **verified** emails are kept, nobody is enriched
  twice, and each search remembers where it left off.

## Going live

Only when your inboxes have warmed up for 2–3 weeks and test emails land in the inbox (not spam):

1. In `config.yaml`, set `test_mode: enabled: false`.
2. Keep `review: enabled: true` for the first weeks.
3. Start at `daily_limit: 10` per inbox and raise it by about 5 a week, up to around 30.

### Run it automatically (optional)

The **Run bot now** button is enough to start with. For hands-off running, schedule it hourly
on an always-on computer or small server.

- **Mac/Linux** (`crontab -e`):
  ```
  0 * * * * cd /path/to/outreach-bot && .venv/bin/python bot.py run --limit 10 >> bot.log 2>&1
  30 8 * * 1-5 cd /path/to/outreach-bot && .venv/bin/python bot.py apollo-pull >> bot.log 2>&1
  ```
- **Windows:** Task Scheduler → Create Basic Task → hourly →
  program `C:\path\to\outreach-bot\.venv\Scripts\python.exe`, arguments `bot.py run --limit 10`,
  start in `C:\path\to\outreach-bot`.

To reach the CRM from other computers: set `CRM_PASSWORD` in `.env`, then run
`python crm.py --host 0.0.0.0`, ideally behind HTTPS.

## Command line (optional)

```bash
python bot.py import leads.xlsx      # import a spreadsheet
python bot.py run --dry-run --force  # print the emails without sending anything
python bot.py run                    # draft / send / check replies
python bot.py check                  # check replies only
python bot.py apollo-preview         # free Apollo preview
python bot.py apollo-pull --max 10   # pull 10 verified leads
python bot.py stats
python bot.py suppress someone@x.com
```

## Deliverability checklist

- Send from a **secondary domain**, never your main one.
- Set up **SPF, DKIM and DMARC** on it.
- Warm up new inboxes 2–3 weeks, and keep under 30–50 emails a day per inbox.
- Keep the bounce rate under 2%. Verified emails only.
- Plain text, few links, no attachments in the first email.
- Honour opt-outs and include your postal address (CAN-SPAM). Take care with UK/EU leads (GDPR).
  Australia is excluded by default (Spam Act).

## Files

| File | What it is |
|---|---|
| `start.bat` / `start.command` | Double-click to start |
| `config.yaml` | Your settings, email copy and Apollo searches |
| `.env` | Secret keys (never share this file) |
| `crm.py`, `crm_templates.py` | The CRM |
| `bot.py`, `apollo.py` | The automation |
| `outreach.db` | Your data (created on first run; back it up) |
