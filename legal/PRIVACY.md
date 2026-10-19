# BSO — Privacy Policy

Version {{version}} · Effective {{effective_date}}

This Privacy Policy explains how the BSO software (the "Software"), published by **{{company_name}}** ("BlacSwanken", "we"), handles personal information. In short: **the Software runs on your computer, your data stays on your computer, and BlacSwanken never receives it.** Data leaves your computer only to reach services you choose to connect, as described below.

## 1. BlacSwanken does not collect your data

The Software has no accounts, no analytics, no telemetry and no advertising. We do not receive, host, sell or share the data you store in it. If you obtained the Software from the Microsoft Store, Microsoft may collect installation, usage and crash information under the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement); that collection is controlled by Microsoft, not by us.

**Team mode is the one exception, and it is optional.** If your employer or manager gives you an ID and password and you sign in to their team, the Software reports your outreach activity to *their* hub, as described in section 7. That hub is run by your organisation, not by BlacSwanken, and BlacSwanken receives nothing from it.

## 2. Information the Software processes on your device

| Information | Where it comes from | Why |
| --- | --- | --- |
| Contact (lead) details: name, work email, job title, company, website, LinkedIn URL, location, notes | Files you import, details you enter, or the Apollo.io service if you connect it | To manage your sales pipeline and send your emails |
| Email drafts, sent emails, replies, bounce notices and activity history | Created by the Software, or read from the inbox you connect | To run your email sequences and detect replies |
| Your inbox address, sender name, settings and email templates | Entered by you | To send email on your behalf |
| Passwords and API keys | Entered by you | To connect to your email provider and the services you choose |

When checking for replies, the Software reads recent messages in the inbox you connect. It acts only on messages from contacts already in the Software and on delivery-failure notices. For other messages it keeps only a technical message identifier, so the same message isn't processed twice.

## 3. Where data is sent

Data is sent only to services you configure, only to perform the feature you switched on:

| Service | What is sent | When |
| --- | --- | --- |
| Your email provider (e.g. Google, Microsoft) | The emails you approve, and your login, to send them; your login, to read replies | When the Software sends email or checks for replies |
| Anthropic (AI) | For opening lines: the contact's company name, job title, industry and text from the company's public website. Never the contact's name or email address | Only if "AI-written line" is on and an Anthropic key is set |
| Anthropic (AI) | The text of replies you receive | Only if "Use AI to sort replies" is switched on (off by default) |
| Company websites | A standard web request for the public homepage of a contact's company | When writing an AI opening line |
| Apollo.io | Your search filters (job titles, regions, company sizes, industries) and the Apollo IDs of people to look up | Only if you add an Apollo key and use Find leads |
| Your organisation's team hub | Only after you join a team: see section 7 | About every two minutes while the Software is open, and after each sending run |
| Slack | A notification with the contact's name, email, company and the start of their reply | Only if you add a Slack webhook |

Each service handles this data under its own terms and privacy policy, including [Anthropic's](https://www.anthropic.com/legal/privacy), [Apollo.io's](https://www.apollo.io/privacy-policy) and [Slack's](https://slack.com/trust/privacy/privacy-policy).

## 4. How data is protected

- Passwords and API keys are encrypted with Windows Data Protection (DPAPI), so only your Windows user account on your computer can read them.
- Connections to email providers and online services use encrypted connections (TLS/HTTPS).
- Your contact database is stored in your Windows user profile, protected by your Windows account. For extra protection, use device encryption such as BitLocker and a strong Windows password.

## 5. Your role and your contacts' rights

When you use the Software to process information about your contacts, **you** decide what is collected and why, so you are responsible for it under data protection law (often called the "controller"). The Software helps you meet your obligations:

- **Do not contact**: permanently stops all emails to an address, and blocks it from being imported again. Unsubscribe replies and bounces are added automatically.
- **Delete lead**: erases a contact and all their history from the Software.
- **Export**: exports your leads to a CSV file, for example to answer an access request.

If a contact asks BlacSwanken about their data, we will tell them that we do not hold it and direct them to you, the sender.

## 6. Retention and deletion

Data stays on your device until you delete it. Uninstalling the Microsoft Store version removes the Software's data folder. If you used the separate installer, you can delete the folder `%APPDATA%\OutreachCRM` after uninstalling to remove all data, including encrypted keys.

## 7. Team mode (only if you join a team)

Team mode lets a manager oversee a group of people using the Software. It does nothing unless you sign in with an ID and password issued by your organisation and tick a box confirming you have read the disclosure shown on the sign-in screen. If your organisation installs the Software for you in managed form, you must sign in to use it, and the Software stores a salted, hashed check of your password on your computer so you can sign in when the hub is briefly unreachable. After you join, the Software sends your organisation's hub:

- each email sent, drafted, approved or rejected, each bounce, and the label of each reply (for example "interested"), with the lead's email address and company, the sequence step and which of your inboxes was used;
- when this computer last connected and which version of the Software it runs;
- if your manager requires approval, the subject and text of drafts waiting for approval.

It never sends your passwords, API keys, inbox logins, your lead list, your notes or the text of replies. The hub can tell the Software to pause sending, apply a daily limit, or approve, edit or reject a draft. The hub's owner (your organisation) is the controller of the data it holds and is responsible for telling you how long it keeps it and for handling your requests about it; BlacSwanken does not host, see or process it. If the Software was not installed in managed form, you can leave the team at any time in Settings → Team. Either way, the hub's owner keeps what it already received. Your organisation may have its own policies, and we recommend that it tells you about team monitoring before you join, as the law in many places requires.

## 8. Children

The Software is a business tool and is not intended for anyone under 18.

## 9. Changes

We may update this Privacy Policy. The Software will show the updated version and its effective date.

## 10. Contact

{{company_name}}, {{address}}. Email: {{contact_email}}
