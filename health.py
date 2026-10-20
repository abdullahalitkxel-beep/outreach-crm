"""Sending-domain health: checks the DNS records that decide whether your emails land in the inbox.

SPF (which servers may send for the domain), DKIM (the signature on each email), DMARC (the policy telling
receivers what to do with mail that fails) and MX (where replies go). Looked up through Google's public DNS
over HTTPS, so it works on any network.
"""
import json

import requests

from bot import iso, now_utc

DOH = "https://dns.google/resolve"
DKIM_SELECTORS = ["default", "google", "selector1", "selector2", "k1", "k2", "s1", "s2", "dkim", "mail", "smtp", "sig1", "mxvault", "zoho", "titan1"]


def _txt(name, kind="TXT"):
    r = requests.get(DOH, params={"name": name, "type": kind}, timeout=15)
    r.raise_for_status()
    out = []
    for a in r.json().get("Answer") or []:
        data = str(a.get("data", ""))
        out.append(data.replace('" "', "").strip('"') if kind == "TXT" else data)
    return out


def check(domain):
    """{'spf': (ok, detail), 'dkim': …, 'dmarc': …, 'mx': …, 'score': 0-4, 'fix': [what to do]}."""
    res, fix = {}, []
    try:
        spf = [t for t in _txt(domain) if t.lower().startswith("v=spf1")]
        if len(spf) == 1:
            res["spf"] = (True, spf[0][:120])
        elif len(spf) > 1:
            res["spf"] = (False, "more than one SPF record (only one is allowed)")
            fix.append("SPF: merge the SPF records into one TXT record starting v=spf1.")
        else:
            res["spf"] = (False, "missing")
            fix.append("SPF: add a TXT record on the domain from your email host (e.g. v=spf1 include:_spf.google.com ~all for Google, or SiteGround's value).")
        dm = [t for t in _txt("_dmarc." + domain) if t.lower().startswith("v=dmarc1")]
        if dm:
            policy = next((p.split("=", 1)[1].strip() for p in dm[0].split(";") if p.strip().lower().startswith("p=")), "none")
            res["dmarc"] = (True, f"policy {policy}")
        else:
            res["dmarc"] = (False, "missing")
            fix.append("DMARC: add a TXT record at _dmarc." + domain + " with: v=DMARC1; p=none; rua=mailto:you@" + domain)
        found = None
        for sel in DKIM_SELECTORS:
            if any("p=" in t for t in _txt(f"{sel}._domainkey.{domain}")):
                found = sel
                break
        res["dkim"] = (bool(found), f"selector {found}" if found else "not found with the usual selector names")
        if not found:
            fix.append("DKIM: switch on DKIM signing at your email host (Google Admin → Apps → Gmail → Authenticate email; SiteGround → Email → Authentication) and add the TXT record it gives you.")
        mx = _txt(domain, "MX")
        res["mx"] = (bool(mx), ", ".join(m.split()[-1].rstrip(".") for m in mx[:3]) if mx else "missing")
        if not mx:
            fix.append("MX: the domain can't receive replies; add your email host's MX records.")
    except requests.RequestException as e:
        return {"error": f"Couldn't look up the DNS records ({e})."}
    res["score"] = sum(1 for k in ("spf", "dkim", "dmarc", "mx") if res[k][0])
    res["fix"] = fix
    return res


def check_all(cfg, con):
    domains = sorted({s["email"].split("@")[1].lower() for s in cfg.get("senders") or [] if "@" in s.get("email", "") and "example" not in s["email"]})
    out = {}
    for d in domains:
        r = check(d)
        out[d] = r
        con.execute("INSERT OR REPLACE INTO domain_health(domain, at, result) VALUES (?,?,?)", (d, iso(now_utc()), json.dumps(r)))
    con.commit()
    return out
