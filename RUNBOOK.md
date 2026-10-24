# Runbook: the BSO Bot laptop is dead or missing

What happens on its own, what you do, and in what order. Keep a printed copy with the spare laptop.

> **Known gaps until the next release** (from the October 2026 audit):
> - With the Google relay, **revoking a laptop in Devices doesn't stop it**. Before signing in on the spare, use **Users → BSO Bot → Disconnect laptops** (or reset the bot's password) so the old laptop can't come back and send too.
> - A spare laptop has no send journal from the dead one: leads that were already overdue at backup time could get one repeated email. Check the Sent folders of the busiest inboxes before pressing Start sending.

## What happens on its own

- **Sending stops.** Nothing goes out while the bot is down. That's the safe way to fail.
- **Replies wait in the inboxes.** They're on Google's or SiteGround's servers, not on the laptop, so none are lost. That includes stop requests and unsubscribe-link clicks.
- **You're alerted.** The Master Console emails you when the bot hasn't checked in for 2 hours, and also when it is online but has sent nothing for 3 working hours while leads are due (a stuck run, everything paused). These alerts need the Master Console running and its email set up.
- **Unsubscribe links keep working.** Your Google relay records the clicks while the bot is down. The bot applies them when it's back, before it sends anything.

## The first hour: is it really dead?

1. **Check the basics:** power and charger, Wi-Fi, and whether Windows is stuck on an update screen.
2. **Restart and wait.** If the laptop restarts, BSO starts with Windows. It reads every inbox before it sends a single email.
3. **Look at the console.** Master Console → **BSO Bot** shows when the bot last checked in.

If it comes back, you're done. BSO settles any email that was cut off mid-send by checking that inbox's Sent folder first.

## If it won't come back: move the bot to the spare laptop

You need:
- the spare laptop;
- the **BSO Bot** user's ID and password;
- nothing else: the backups are in your Google Drive (folder **BSO Team Data → backups → the bot's ID**), and BSO fetches them itself.

1. **Disconnect the old laptop.** Master Console → **Devices**: revoke the dead laptop, in case it comes back later or was stolen.
2. **Prepare the spare.**
   - **Power:** plugged in, sleep *Never*, lid *Do nothing*.
   - **Encryption:** BitLocker on.
3. **Install BSO and sign in as BSO Bot.** Approve the new laptop in Master Console → **Devices**. The sign-in brings back:
   - the inboxes and passwords;
   - the AI key;
   - the team key.
4. **Restore the latest backup.** BSO Bot page → **Backups** → **Restore a backup**.
   - Click **Show backups on Google Drive** and pick the newest one (it's first).
   - Type `RESTORE` and press **Restore from Google Drive**.
   - No Google Drive copy? Use a backup file instead: paste its full path in the second box and press **Restore file**.
   - Quit BSO from the tray icon and open it again. The backup goes in as BSO starts.
5. **Check what BSO settled.** A backup is up to 6 hours old, so emails sent after it aren't in it. BSO settles that gap the cautious way:
   - **Follow-ups due since the backup:** counted as sent, so nobody gets the same follow-up twice. A few people may miss one follow-up.
   - **First emails due since the backup:** held, and listed under **Paused by the safety breaker → After a restore**. Look at those inboxes' Sent folders. If you're unsure, leave them held. Otherwise press **Resume** to send them.
   - **Sending:** stays stopped until you press **Start sending**.
6. **Start sending.** BSO first reads every inbox (from before the outage, however long it lasted), then any unsubscribe-link clicks waiting in the relay, and waits for the team's do-not-contact list from the Master Console. Only then does it send.
7. **Backups carry on by themselves** on the new laptop, to the same Google Drive folder. If you also used a backup folder, set it again: BSO Bot page → **Backups** → **Also copy to a folder**.

## If there is no backup on Google Drive or anywhere else

The bot starts empty. The inboxes, campaigns, clients and domains come back from the Master Console setup. The lead history doesn't.

- **Re-import leads only** with the lead template, and only people you haven't contacted. The console's conversation copies and the inboxes' Sent folders show who was contacted.
- **Never re-import a list you already sent to.** BSO would treat those people as new.

## Afterwards

- **Find the cause:** hardware, power, Windows Update or theft. Note it on the incident record in the console.
- **Confirm the backups:**
  - BSO Bot page → **Backups** shows "restore test passed";
  - "Google Drive copy: saved and checked" appears within 6 hours.
- **Plan to stop needing this runbook:** the standby bot (release 3) watches the main bot and takes over without you.
