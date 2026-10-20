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

## Running BSO as a bot (recommended)

Let one BSO run every campaign by itself and make every person a Lead Gen Supervisor:
1. **Users → Add a user**, role **BSO Bot**. Sign in to BSO with it on the computer that will run the campaigns. It stays signed in after restarts; people's accounts always need their password.
2. On that computer, in BSO: connect all sending inboxes (Settings → Email inboxes), then Settings → Safety & automation → **Run automatically** and **Start BSO when Windows starts**. While automatic runs are on, BSO keeps Windows awake. On a laptop also set Windows → Settings → System → Power → *Lid close action: Do nothing* (plugged in), and keep it plugged in and online.
3. **Leads by email**: Settings → Lead targeting → *Leads by email*: choose the intake inbox and who may send files (e.g. omar@blacswanken.com). Email a CSV/Excel file (an Instantly export works) to that inbox; the bot imports it on its next run and replies with what it added.
4. **More leads without anyone importing them** (BSO → Settings → Lead targeting):
   - *Referrals*: when a reply says "talk to Sarah, sarah@acme.com", Sarah is added automatically and her first email opens "John suggested I reach out".
   - *New leads from Instantly*: with your Instantly API key and filters, the bot imports fresh SuperSearch leads whenever fewer than N days of leads are left.
   - *Find more contacts on the web*: the AI looks for decision-makers whose work email is published online; BSO only adds addresses it can see on the page named, at the company's domain (and verified by Instantly when that key is set).
5. Add the people as **Lead Gen Supervisors**. Interested prospects go to them in turn.

The Master Console's Overview shows a **Bot** card: online, last run, sent and replies today, prospects handed over, leads not yet emailed and days of leads left, with warnings when it hasn't run, a run failed, test mode is still on or leads are running out.

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

## 5. Security notes

Passwords and device tokens are stored hashed (the hub never keeps a readable password); sign-in and invite attempts are rate-limited; forms are CSRF-protected; sessions expire after 12 hours. The hub never receives anyone's inbox passwords, API keys, lead lists or reply text. Tell your team before you switch this on: the Privacy Policy section 7 covers it, but your own employment rules may add duties.

A person with full control of their own Windows account could tamper with their local copy of BSO. The hub will then simply stop hearing from that laptop (the Overview shows it as Offline), which is how you would notice.
