# BSO master hub: setup guide

The hub is the manager's console. Team members keep BSO on their own laptops; each laptop connects to the hub with a one-time invite.

## 1. Run the hub (on one always-on computer)

- Windows app: Start menu → **BSO Master Console** (it runs `BSO.exe --hub` and opens the console). To let other computers connect, run `BSO.exe --hub --host 0.0.0.0` instead.
- From source: `pip install -r requirements.txt` then `python hub.py --host 0.0.0.0 --port 8780`.

On first start the console opens on the setup page: enter your team name, name, email (pre-filled as omar@blacswanken.com) and a password of 10+ characters. On the hub computer itself no setup key is needed. From any other computer the page asks for the **setup key** saved as `setup-key.txt` in the hub data folder (`%APPDATA%\OutreachCRM\hub`); it is deleted once the owner exists. To stop the console: Settings → Stop the master console.

The hub's data (users, activity, drafts awaiting approval) is in `hub.db` inside the hub data folder (`<BSO data folder>\hub` by default). Back it up.

## 2. Connect your Google Drive (recommended: no server, no domain)

Team laptops sign in and report through a small Google Apps Script that runs under **your** Google account. Their reports are kept in a private **BSO Team Data** folder in your Drive; your console collects them about once a minute whenever it's open. Team members need no Google account and no sharing. Your laptop doesn't have to stay on: reports wait in Drive until your console next opens.

1. In the master console: **Settings → Team connection through your Google Drive → Copy script.** The script is unique to your console; keep it private.
2. Go to script.google.com (signed in to the account that should hold the data) → **New project**. Delete what's there, paste, **Save**.
3. **Deploy → New deployment →** gear icon → **Web app**. Execute as: **Me**. Who has access: **Anyone**. **Deploy.**
4. **Authorize access**, choose your account. Google warns the app isn't verified because it's your own script: **Advanced → Go to … → Allow**.
5. Copy the **Web app URL** (https://script.google.com/macros/s/…/exec), paste it into the console and click **Save & test**.
6. That address must be built into the BSO your team installs (or put in `team-defaults.json` next to `BSO.exe`).

To update the script later, use **Deploy → Manage deployments → Edit → Version: New version**, which keeps the same address. A brand-new deployment gets a new address, and team laptops would need it.

Limits: Google's free quotas are comfortably enough for a team of about 20–30 people checking in every couple of minutes.

## Alternative: your own server

Instead of Google Drive, the console can serve the team directly from an address such as https://hub.your-company.com. That address has to lead to the computer running the master console. The simplest free way is a Cloudflare Tunnel (no open ports, HTTPS included):

1. Add the blacswanken.com domain to a free Cloudflare account (Cloudflare shows the two name-server addresses to set at your domain registrar).
2. In Cloudflare: Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared. Install the connector it shows on the hub computer.
3. Add a public hostname: subdomain `hub`, domain `blacswanken.com`, service `http://localhost:8780`.
4. Open the address: the master console sign-in page should appear.

The hub computer must stay on and awake while the team works. Until the address is live, BSO on the hub computer itself signs in to the local console automatically; other laptops can't sign in yet.

## Other ways to use HTTPS

BSO laptops refuse to join a plain `http://` hub unless it is on a private network address. Choose one:

- **Cloudflare Tunnel (no open ports, free):** `cloudflared tunnel --url http://localhost:8780` gives an `https://….trycloudflare.com` address; use a named tunnel with your own domain for a permanent one.
- **Caddy on a small server:** a `Caddyfile` of `hub.your-company.com { reverse_proxy 127.0.0.1:8780 }` gets a free certificate automatically. Run the hub with `--host 127.0.0.1`.

Then open **Settings** in the console and enter that address as *Public address of this hub*. Invites carry it.

## Forgotten master password

- **By email:** on the sign-in page click *Forgot password?*, enter your email or ID, and open the link that arrives (works once, valid 30 minutes). This needs outgoing email set up first: console **Settings → Outgoing email** (for Gmail use an app password), then *Send a test email to me*. Also set the hub's public address there so the link opens the right place.
- **If email isn't set up and you're locked out:** on the hub computer run `BSO.exe --hub --reset-owner-password` and type a new password.

## 3. Add people and install BSO for them

1. **Users → Add a user**: name, email, sign-in **ID**, role, daily cap, weekly target, and who approves their emails. Leave the password blank to have one generated.
2. The ID and password are shown **once**. Give them to the person privately.
3. Install BSO on their computer as normal. Every time BSO opens it shows the sign-in screen. The first time, they also type the hub address you give them. To fill the address in for them, install with `BSO-Setup.exe /HUB=https://hub.your-company.com` or put `team-defaults.json` (from the hub's **Settings** page) next to `BSO.exe`.
4. The person opens BSO, signs in with the ID and password, and on first sign-in ticks the box after reading what you can see.

Until someone signs in, BSO opens nothing and sends nothing, including automatic runs. If the hub can't be reached, the same person can still sign in on that laptop for the grace period (default 72 hours).

Roles: **owner** (everything, including users and settings), **manager** (sees everyone, approves drafts, signs in to the console), **member** (uses BSO on their laptop).

## Set up every BSO from the console (no settings on the laptops)

1. Master Console → **BSO setup**: enter the mail server (SiteGround: `mail.getneosofttech.com`, SMTP 465, IMAP 993), emails a day per inbox, warm-up, the Anthropic key (and Instantly key if used), and how often campaigns run. Save.
2. **Users → Add a user** (or the BSO Bot): in *Their sending inboxes* paste one line per inbox: `email, name prospects see, password`.
3. On the laptop: install BSO, sign in with the ID and password you created, accept the terms. BSO sets itself up straight away: inboxes, passwords, keys, schedule.

Later changes (a new inbox on the user's page, a new key or schedule in BSO setup) reach each BSO at its next check-in; nobody signs in again. Many inboxes at once: **BSO setup → Import inboxes from a spreadsheet** (download the Excel template there). The user page and BSO setup show **applied** once a BSO has it. Inbox passwords and keys are stored encrypted on the console computer and travel encrypted with the person's sign-in password, so Google Drive only ever holds scrambled data. For people created before this existed, enter their current BSO password once in the setup box on their page (or reset their password).

## Running BSO as a bot (recommended)

One BSO account, **BSO Bot**, runs cold outreach for every client on one always-on laptop. Nobody presses Run; people act only on exceptions. The full rules are in the *BSO Bot: Rules of Engagement* document.

**Set it up once**
1. **Users → Add a user**, role **BSO Bot**, approval *The master approves every email*. Load its inboxes with **BSO setup** (spreadsheet), then sign in to BSO with it on the bot laptop. It stays signed in, starts with Windows, keeps Windows awake and checks every 3 minutes. Its screen lock never stops the work.
2. Laptop: plugged in, sleep *Never*, lid *Do nothing*, BitLocker on, Windows Update active hours 07:00–23:00.
3. In the bot's BSO, **Lead intake** page: connect the intake mailbox (e.g. leads@blacswanken.com). Only the owner's address is accepted, and only when the mail server confirms it (SPF/DKIM/DMARC).
4. **Master Console → BSO Bot**: add the clients (with their line of business), their sending domains, and choose each campaign's domains, autonomy, arrival window and information answers. A domain belongs to one client only.
5. Cold inboxes live only on the bot. Supervisors connect only their own mailbox on each client's brand domain (Settings → My mailbox & away).

**Hundreds of inboxes**: the bot takes 10 or 1,000 inboxes on as many domains as you like. Load them with the BSO setup spreadsheet or in bulk (Settings → Email inboxes: add, update or remove hundreds at a time, or a whole @domain). Each round, every inbox that may send writes and sends at the same time as the others (12 at once), still at most one email per inbox per round; replies are read in parallel; the first 200 inboxes keep a live connection for instant replies and the rest are read every 10–15 minutes.

**How it runs**
- Each inbox's daily limit follows its reputation (bounces, unsubscribes, blocklists, spam-placement tests, SPF/DKIM/DMARC), between 5 and 30. A new inbox (warmed up in Instantly) passes a readiness check, then sends at half volume for 5 working days. Bounce or login pauses resume by themselves after 24 hours at half volume; blocklisting waits for you.
- First emails arrive between 08:45 and 10:30 in the lead's own time zone, on their working days, skipping public holidays; follow-ups use normal working hours; 6–20 minutes between two emails from one inbox.
- Contact rules: 2 emails a day per recipient domain, one active sequence per company, 3 people per company per 30 days, never two first emails to one person within 7 days, 90 days between clients in the same line of business.
- Autonomy per campaign: **Assisted** (every email waits for approval), **Supervised** (high-confidence emails go alone, 1 in 20 kept as a spot check), **Autonomous** (everything that passes the quality check, 1 in 50 spot checks).
- Days of lead stock count the follow-ups owed first. Below 10 days you get an email with the lead template attached (repeated every 2 days, urgent below 3). Reply with the filled file; BSO sends a receipt and a file of rejected rows.
- 08:00 daily digest; email **STOP ALL** / **START ALL** to the intake mailbox, or use **Stop all sending** on the BSO Bot page. You're emailed if the bot hasn't checked in for 2 hours.

**Safety rules (always on)**
- **Stop requests are decided in code.** "Unsubscribe", "remove me", "this is spam", one-word "STOP", the main European languages, and the mailto: unsubscribe all stop every future email at once, even for a prospect already handed to a supervisor. Legal or data-protection wording (GDPR, "where did you get my email", lawyer) also stops, alerts the owner and makes a task for a person to answer. Only the prospect's own words count: our footer quoted under a Gmail or Outlook reply never triggers a stop. The AI can't overrule any of this.
- **A reply can't give the AI orders.** The AI's instructions sit in its system prompt; reply and website text are passed as marked outside text. A reply that tries to instruct the AI ("ignore your instructions…") goes to a person and is never answered automatically. Automatic information answers may only choose *answer*, *hand over* or *stop*, and an answer is sent only if it has no links, addresses, phone numbers or figures beyond the approved facts.
- **One-click unsubscribe.** Cold emails carry an unsubscribe link (Gmail's and Yahoo's button) served by your Google relay. The link holds a random code, never the address; opening it shows a button, and only pressing it (or Gmail's button) unsubscribes. This needs the Google script version 5: Master Console → Settings shows the one-time update.
- **No double sends.** Each email is written down before it goes and marked sent after. If the laptop dies in between, BSO checks that inbox's Sent folder before trying again. Hosts that keep no Sent copy (SiteGround SMTP) count it as sent: a missed email costs less than a repeated one.
- **Replies first.** An inbox sends cold email only if its replies were read in the last 45 minutes. After an outage every inbox is read before anything goes out.
- **A last check before every email.** Right before the mail server is contacted, BSO re-reads the lead: if they replied, unsubscribed or went on do-not-contact a moment ago, the email isn't sent and the lead isn't put back in the sequence.
- **One do-not-contact list for the team.** Every stop, bounce and unsubscribe on any laptop goes to the Master Console as a one-way code (never the address) and on to every other laptop. A new, rebuilt or restored bot sends no cold email until it has received the whole list. An import can't bring back anyone on it.
- **Test mode keeps real stops.** Going live only undoes what your own test replies did; a real person who replied "stop" while you were testing stays stopped. Test emails carry no real unsubscribe link.
- **Replies are never lost.** An email is marked done only after it's handled. If handling fails it's read again (and the inbox doesn't count as read meanwhile); after 3 failures a stop-like email still stops the sender and a person gets a task. After an outage, replies are read from the last successful read, however long ago.
- **Stuck runs.** A run left "running" by a crash, power cut or update restart is closed when BSO starts; a run that shows no progress for 45 minutes (or runs for 8 hours) is stopped; only one run at a time (a real lock, also on Windows); every run page has a Stop button. The Master Console emails you when the bot is online but hasn't sent anything for 3 working hours while leads are due, with the likely cause.
- **Circuit breaker.** An inbox, a campaign or an imported list (one uploaded file) pauses itself when, over at least 50 emails, 5% bounce, 3% ask to stop, or 3+ complaints arrive. Pausing is automatic; resuming is yours (BSO Bot page → Paused by the safety breaker). "Mailbox full" bounces count as temporary: the address stops only after 3.
- **Auto-replies** (by their headers) and help-desk ticket receipts aren't replies and don't count in reply rates. An out-of-office without a return date delays the next email by 3 working days.
- **Backups every 6 hours**, each one restore-tested. With the Google script at version 6, each backup also goes to your Google Drive (BSO Team Data → backups), sealed with the team key, read back and checked; the newest 14 are kept. Nothing to set up. A backup folder can be added as well (BSO Bot page → Backups). If the bot laptop dies, follow [RUNBOOK.md](RUNBOOK.md).

## Clients: the Scope room
**Clients → New scope**: upload a client's scope document (PDF, Word, email or text). BSO fills a Scope card (every field with the sentence it came from), lists questions for the client, works out the leads, inboxes, time and AI cost the target needs, flags conflicts (LinkedIn or phone steps, consent rules by country, figures to back up), and proposes a campaign. **Approve** sends it to the bot (starting in Assisted). A new version shows what changed; progress against the scope appears on its page. Copy the client summary to confirm what was agreed.

## Roles
- **Owner** (you): every module in BSO and the console, no locks. **Act as** (BSO menu): *BSO Bot* runs the bot's rules, scheduler and intake on your computer; *Supervisor* makes the console hand prospects to you; full access stays on in both. For UAT.
- **Manager**: a supervisor who can also use this console, without the owner pages.
- **Supervisor**: works the prospects the bot hands over (My day, Conversations, Pipeline, Tasks). Cold inboxes are not used or read on their BSO.
- **BSO Bot**: runs all cold outreach.
(The old *Member* role is retired: existing members became supervisors.)

## Devices and updates (owner)
**Devices** lists every laptop joined with a setup code. A second laptop for the same person waits for your approval. Every BSO checks GitHub every 6 hours and installs only releases signed by BlacSwanken's build system: your BSO first, supervisors 6 hours later, the bot 24 hours later when no email is due. **Hold** stops all updates; a **minimum version** forces an urgent fix. On joining, BSO trusts the BlacSwanken publisher certificate for that Windows user (Windows asks once).

## Who needs to be online? Nobody, at the same time

Your Google script is the always-on middle. Each person's BSO talks only to it, whenever that person is online:
- a sender's BSO hands prospects over and reports activity;
- a supervisor's BSO collects their queue and reports stages;
- your Master Console, whenever you open it, collects everything that happened and publishes your changes (users, limits, pauses, approvals), which each person picks up at their next sign-in.
Only decisions that need a person (for example a draft waiting for your approval) wait for that person.

After updating BSO, update the script once: Master Console → Settings shows a red box with the steps when the script in Google is older than the one BSO ships (copy, paste over, Deploy → Manage deployments → Edit → New version). The address doesn't change.

## Handing interested prospects to supervisors

Add one or more users with the role **Lead Gen Supervisor** (Users → Add a user). They sign in to BSO like everyone else and get a **My prospects** page.

When a prospect replies **interested**, asks for a **meeting** or asks a **question**, the BSO that received the reply:
1. sends the prospect a short introduction from the same inbox, in the same email thread, with the supervisor in CC (the wording is in BSO Settings → Handover);
2. emails the supervisor a briefing with the prospect's details, their reply and every email sent to them;
3. stops all automatic emails to that prospect and marks them as handed over.

Supervisors take turns: Google (your BSO relay script) decides whose turn it is, so it's fair across the whole team. Pausing a supervisor stops new prospects going to them. The prospect waits in that supervisor's own queue in your Drive and appears under **My prospects** as soon as they sign in, on any computer; a new laptop gets all their prospects back with their stages. Nobody needs anyone else, including you, to be online; the supervisor sets the stage there (Contacted → Meeting booked → Proposal sent → Won / Lost), and you follow it on the **Handovers** page, including how many hours each supervisor took to make a first update.

In test mode, the introduction and the briefing both go to the test address instead.

**Supervisors work in one window.** In BSO, Settings → Handover → *Your mailbox*, each supervisor connects the mailbox they're introduced to (Google Workspace: imap.gmail.com / smtp.gmail.com with an app password). Each prospect's page in **My prospects** then shows the whole conversation (including replies sent from their normal email app), has a reply box that sends from their mailbox in the same thread, and new replies are flagged and counted in the menu. If a handed-over prospect writes to the original sender's inbox again, that BSO forwards it to the supervisor straight away and it appears in the conversation, so nothing slips through.

## Working together on prospects

- **Prospect board.** Supervisors see *My prospects* as a board (New → Contacted → Meeting booked → Proposal sent → Won / Lost) and drag cards between columns; Won and Lost ask first. The console's **Prospect board** shows everyone's, filterable by supervisor and campaign; red-edged cards haven't been touched for 2+ days.
- **Comments with @mentions.** On a prospect's page, in BSO or in the console, write a comment and mention people with @ and their first name. It reaches the people mentioned and everyone already in that prospect's comments, at their next sign-in. Mentions of you on someone else's prospect appear under *You were mentioned*.
- **Away.** A supervisor marks themselves away (BSO → Settings → Handover → Away), or you set *Away until* on their user page. While away they get no new prospects.
- **Moving a prospect.** Open it in the console (Prospect board → card) → *Move to another supervisor*. It leaves the old supervisor's list (with a note) and arrives in the new one's with its conversation and stage.
- **Removing someone.** Users → the person → *Remove user*. Their sign-in stops at once. A supervisor's open prospects are shared equally among the other supervisors who aren't away. A sender's BSO stops sending and, at its next check-in, hands over its leads, which are shared equally among the remaining senders (not-yet-emailed leads carry on; leads part-way through a sequence arrive paused with a "continue or restart" task; the do-not-contact list goes to everyone). If that laptop won't come back online, use *Remove now without their leads*.
- **Search** in BSO covers leads, prospects, emails with prospects, comments, notes and campaigns.

These need the Google script version 3: the console shows the update steps (copy, paste over, Deploy → Manage deployments → Edit → New version).

## 4. What you control

- **Pause / resume** an account: their BSO stops sending at its next check-in.
- **Daily cap** and **weekly target**: the cap is enforced on the laptop; the target shows as progress.
- **Approval**: "The master approves every email" holds their drafts; you edit, approve or reject on the Approvals page and the email goes out from their own inbox on their next run.
- **Reset password** signs that person's laptops out until they sign in with the new one. **Disconnect laptops** signs them out without changing the password.
- **Audit log** (owner) records every change made in the console.

If a laptop can't reach the hub for the grace period (default 72 hours, adjustable in Settings), it pauses sending.

## 5. Security page (owner)

Master Console → **Security**. Everything here reaches every BSO at its next check-in.

- **2-step sign-in** (top bar → *2-step sign-in*): set it up first, then it switches on. Click *Set up*, add the key to Google or Microsoft Authenticator, type the 6-digit code, and save the 8 recovery codes it shows (each works once). From then on the console asks for your password and a code. Lost your phone and your codes? On the console computer run `"BSO Master Console.exe" --reset-owner-2fa`.
- **Idle lock**: BSO goes back to the sign-in screen after this many minutes without use (default 15; the BSO Bot account is never locked).
- **Re-check every N days**: when the internet is down a person can still sign in on their own laptop, but only for this many days since their last online sign-in (default 7).
- **Exports**: only owners and managers can export leads to CSV or open the data folder. Members get a "not allowed" screen, and the attempt is logged. Every export, every opened data folder and every list bigger than the "big list" number shows in the **Data access log**, with an alert for the last 7 days.
- **Trap contacts**: enter a domain you own that has a catch-all mailbox (a cheap spare domain is ideal). The console gives every sending account 2 fake leads at that domain, different for each person. They look like old finished leads; BSO will never email them. If one ever receives mail, that person's list was copied out, and the alert names whose. Add the catch-all mailbox's IMAP details to have it checked every 30 minutes, or paste a suspicious address into *Check* by hand.
- **Laptops**: shows whether each laptop's drive is encrypted (BitLocker or Device encryption). An unencrypted drive is an alert: if that laptop is stolen, its BSO data can be read.
- **Sign-in guessing**: 5 wrong passwords lock that ID for 15 minutes, then 1 hour, then 6 hours. Repeated failures and current lockouts show as alerts. (Needs the version 4 Google script: Settings shows a button to update it.)

**Erasing a laptop.** Removing a person now also erases their copy: at its next check-in their BSO hands over its leads (shared equally among the others), erases its leads, prospects, emails, notes and saved passwords, confirms, and only then is the account removed. *Erase laptop and disconnect* on a user's page does the same but keeps the account (for a lost laptop or a role change). *Remove now* skips the wait if the laptop is never coming back online.

## 7. Replies: following the conversation

BSO decides whose conversation an email belongs to by the **thread** first (the hidden IDs every mail app keeps), then the sender and the lead's other addresses, the **reference code** under each email (Settings → Safety → Replies), our original quoted inside a forwarded email, and finally the company (flagged "please confirm"). So a reply from a colleague in CC, from the lead's personal Gmail, or from a decision maker the email was forwarded to all land in the right place; forwards show their path (abc → def → xyz). When anyone at a company replies, the other leads at that company stop getting cold emails.

Every reply is sorted 🔥 hot / ☀ warm / ❄ cold / ⛔ closed, with deadlines (hot 2 working hours, warm 1 working day; change them in Settings → Safety). Cold leads go to Nurture and come back on the date they mentioned with a check-back email for review. Each inbox has an **activity log** (Settings → Email inboxes) showing what BSO did with every email, a **Re-check** button, and Spam folders are read for replies to our own threads.

**Emailing a client:** every lead page has an **✉ Email** button (top) and an Email box: it sends in the same thread from the inbox that has been writing to them (or your own mailbox), stops the automatic emails, and blocks copies outside the company and the client.

## 8. Full visibility (Master Console → Conversations, Insights, Security)

- **Conversations**: every lead of every person, by campaign, with every email sent and received (CC and BCC included), supervisors' emails with prospects, notes, tasks and temperatures. Everything each BSO held before this version is in the **Archive**.
- Reports from each BSO are sealed with a key only that BSO and this console have; Google Drive only carries ciphertext. Handovers, comments and lead transfers are sealed too.
- **Security → Incidents**: client emails copied outside (CC/BCC/forward), blocked attempts, deleted client emails, and emails seen only in the company archive mailbox. Entries are permanent and chained; review them and export to CSV. Set the retention period and partner domains there.
- **Google Workspace**: Security → Company archive mailbox links to a step-by-step guide (copy of every supervisor email to an archive mailbox, quarantine for outside copies, Vault).
- **Insights**: the funnel from lead to sale (log scale), results by campaign, person, supervisor, angle, kind of lead and inbox, next best step for open deals, and the weekly **strategy** for the bots: which angle / subject / ask works for which leads, lessons for the writer, and lead scoring. Nothing changes in the bots until you approve it (or allow small changes automatically).
- **AI writing**: each campaign has a **Brief** (Campaign page): with an AI key, every email is written fresh from it; figures may only come from its proof points, and emails too similar to recent ones are rewritten. Settings → AI & Apollo keys shows the month's AI spending and takes a monthly limit.

## 9. Security notes

Passwords and device tokens are stored hashed (the hub never keeps a readable password); sign-in and invite attempts are rate-limited; forms are CSRF-protected; sessions expire after 12 hours. The hub never receives anyone's inbox passwords, API keys, lead lists or reply text. Tell your team before you switch this on: the Privacy Policy section 7 covers it, but your own employment rules may add duties.

A person with full control of their own Windows account could tamper with their local copy of BSO. The hub will then simply stop hearing from that laptop (the Overview shows it as Offline), which is how you would notice.
