# BSO — Privacy Policy

Version {{version}} · Effective {{effective_date}}

This Privacy Policy explains how the BSO software (the "Software"), published by **{{company_name}}** ("BlacSwanken", "we"), handles personal information. In short: **the Software runs on your computer, your data stays on your computer, and BlacSwanken never receives it.** Data leaves your computer only to reach services you choose to connect, as described below.

## 1. BlacSwanken does not collect your data

The Software has no accounts, no analytics, no telemetry and no advertising. We do not receive, host, sell or share the data you store in it. If you obtained the Software from the Microsoft Store, Microsoft may collect installation, usage and crash information under the [Microsoft Privacy Statement](https://privacy.microsoft.com/privacystatement); that collection is controlled by Microsoft, not by us.

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

## 7. Children

The Software is a business tool and is not intended for anyone under 18.

## 8. Changes

We may update this Privacy Policy. The Software will show the updated version and its effective date.

## 9. Contact

{{company_name}}, {{address}}. Email: {{contact_email}}
