# BSO master hub: setup guide

The hub is the manager's console. Team members keep BSO on their own laptops; each laptop connects to the hub with a one-time invite.

## 1. Run the hub (on one always-on computer)

- Windows app: `BSO.exe --hub` (add `--host 0.0.0.0 --port 8780` to let other computers connect).
- From source: `pip install -r requirements.txt` then `python hub.py --host 0.0.0.0 --port 8780`.

On first start it prints a **setup key** (also saved as `setup-key.txt` in the hub data folder). Open the address it shows, enter the key, and create the owner account (password of 10+ characters). The key file is deleted afterwards.

The hub's data (users, activity, drafts awaiting approval) is in `hub.db` inside the hub data folder (`<BSO data folder>\hub` by default). Back it up.

## 2. Put it behind HTTPS (required for anything beyond your own office network)

BSO laptops refuse to join a plain `http://` hub unless it is on a private network address. Choose one:

- **Cloudflare Tunnel (no open ports, free):** `cloudflared tunnel --url http://localhost:8780` gives an `https://….trycloudflare.com` address; use a named tunnel with your own domain for a permanent one.
- **Caddy on a small server:** a `Caddyfile` of `hub.your-company.com { reverse_proxy 127.0.0.1:8780 }` gets a free certificate automatically. Run the hub with `--host 127.0.0.1`.

Then open **Settings** in the console and enter that address as *Public address of this hub*. Invites carry it.

## 3. Add people

1. **Users → Add a user**: name, email, role, daily cap, weekly target, and who approves their emails.
2. Copy the invite line shown (it appears once, works once, expires in 7 days) and send it to the person privately.
3. They paste it in BSO under **Settings → Team**, read the disclosure and tick the box. Their status turns to Online.

Roles: **owner** (everything, including users and settings), **manager** (sees everyone, approves drafts, signs in to the console), **member** (uses BSO on their laptop).

## 4. What you control

- **Pause / resume** an account: their BSO stops sending at its next check-in.
- **Daily cap** and **weekly target**: the cap is enforced on the laptop; the target shows as progress.
- **Approval**: "The master approves every email" holds their drafts; you edit, approve or reject on the Approvals page and the email goes out from their own inbox on their next run.
- **Disconnect laptops** revokes access; the person needs a new invite.
- **Audit log** (owner) records every change made in the console.

If a laptop can't reach the hub for the grace period (default 72 hours, adjustable in Settings), it pauses sending.

## 5. Security notes

Passwords and device tokens are stored hashed; sign-in and invite attempts are rate-limited; forms are CSRF-protected; sessions expire after 12 hours. The hub never receives anyone's inbox passwords, API keys, lead lists or reply text. Tell your team before you switch this on: the Privacy Policy section 7 covers it, but your own employment rules may add duties.
