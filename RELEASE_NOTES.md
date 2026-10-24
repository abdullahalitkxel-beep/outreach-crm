**BSO 1.1**

- **Backups to Google Drive.** Every 6 hours the bot copies its data to the owner's Google Drive, sealed with the team key, read back and checked. BSO keeps the last day, one copy a day for 2 weeks and one a week for 2 months. A replacement laptop signed in as the BSO Bot can restore a copy from the Bot page. This needs the Google script updated to version 6 (Master Console → Settings).
- **Safer restore.** Restoring is now one guided flow with a **Restart BSO now** button. Sending stays stopped until the restore is in place. A restore that's waiting expires after 6 hours, and you can cancel it.
- **Safer updates.** Each update carries a signed version number, so an older installer can't be passed off as a new one. Updates come from a public releases page, so the source code can stay private.
- **Protection from other websites.** A web page open on the same computer can no longer press BSO's buttons.
- **Clearer screens:**
  - your name and role at the top, with Act as and Sign out in one menu;
  - the owner's Bot page says plainly that it shows this computer, not the bot laptop;
  - warnings before anything that could email real people;
  - local times everywhere;
  - menus and tables that fit narrow windows;
  - a guided tour for each role.
