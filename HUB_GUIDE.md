# BSO master hub: setup guide

The hub is the manager's console. Team members keep BSO on their own laptops; each laptop connects to the hub with a one-time invite.

## 1. Run the hub (on one always-on computer)

- Windows app: Start menu → **BSO Master Console** (it runs `BSO.exe --hub` and opens the console). To let other computers connect, run `BSO.exe --hub --host 0.0.0.0` instead.
- From source: `pip install -r requirements.txt` then `python hub.py --host 0.0.0.0 --port 8780`.

On first start the console opens on the setup page: enter your team name, name, email (pre-filled as omar@blacswanken.com) and a password of 10+ characters. On the hub computer itself no setup key is needed. From any other computer the page asks for the **setup key** saved as `setup-key.txt` in the hub data folder (`%APPDATA%\OutreachCRM\hub`); it is deleted once the owner exists. To stop the console: Settings → Stop the master console.

The hub's data (users, activity, drafts awaiting approval) is in `hub.db` inside the hub data folder (`<BSO data folder>\hub` by default). Back it up.

## 2. Put it behind HTTPS (required for anything beyond your own office network)

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
3. Install BSO on their computer so it asks for sign-in and knows your hub, either with `BSO-Setup.exe /HUB=https://hub.your-company.com`, or by downloading `team-defaults.json` from the hub's **Settings** page into the BSO program folder (next to `BSO.exe`).
4. The person opens BSO, signs in with the ID and password, and ticks the box on first sign-in after reading what you can see. That is all they need.

Until someone signs in, BSO opens nothing and sends nothing, including automatic runs. If the hub can't be reached, the same person can still sign in on that laptop for the grace period (default 72 hours).

Roles: **owner** (everything, including users and settings), **manager** (sees everyone, approves drafts, signs in to the console), **member** (uses BSO on their laptop).

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
