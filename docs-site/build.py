#!/usr/bin/env python3
"""Builds docs.idpvault.com from the in-app docs.

SINGLE SOURCE OF TRUTH: frontend/docs/*.html are the docs. This script wraps
those same fragments in a branded static template and emits _site/ for GitHub
Pages. Editing a doc ships it in the app image AND on docs.idpvault.com - no
mirror to keep in sync. (This replaced the GitHub wiki mirror on 7/21/2026.)

Run: python3 docs-site/build.py   (from the repo root; output in _site/)
"""
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "frontend", "docs")
OUT = os.path.join(ROOT, "_site")

# Order + titles mirror DOC_TOPICS in frontend/js/pages/admin.js - keep in sync.
TOPICS = [
    ("getting-started", "Getting started",
     "Deploy IdPVault and connect your first tenant."),
    ("backups", "Backups & snapshots",
     "Encrypted config snapshots, retention, Full-DR capture and restore."),
    ("explorer", "Live State",
     "The provider's current config vs your latest backup."),
    ("drift-events", "Drift & events",
     "Unbacked-changes detection and the change events feed."),
    ("restore", "Config restore",
     "Selective restore with dry-run previews and per-object reports."),
    ("clone", "Clone & promote",
     "Apply a snapshot into another tenant: staging to prod, standby, DR."),
    ("identity", "Users & Access backup",
     "Back up and restore users, memberships, and app assignments."),
    ("alerts", "Alerts & notifications",
     "Email and webhook alerts: drift, failures, restores, clones."),
    ("users-security", "Users & security",
     "App users, roles, MFA, sessions, and the audit log."),
    ("licensing", "Licensing & tiers",
     "Community, Business, and MSP tiers; offline license keys."),
    ("msp-orgs", "MSP & client orgs",
     "Client orgs, scoped roles, renewal tracking, CSV onboarding."),
    ("deployment", "Deployment & proxy",
     "Compose, reverse proxies, public URL, and upgrades."),
]

SITE = "https://docs.idpvault.com"

CSS = """
:root{--bg:#0b0e14;--panel:#11151f;--edge:#1e2534;--text:#d7dce6;--muted:#8b93a7;
--accent:#0080f0;--accent2:#3ea0ff;--gold:#f0c000;--code:#0e1420}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.65 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
a{color:var(--accent2);text-decoration:none}a:hover{text-decoration:underline}
.top{display:flex;align-items:center;gap:14px;padding:14px 22px;
border-bottom:1px solid var(--edge);background:var(--panel);position:sticky;top:0;z-index:5}
.top img{height:30px}
.top .t{font-weight:700;color:#fff;font-size:1.05rem}
.top .t span{color:var(--muted);font-weight:500}
.top .links{margin-left:auto;display:flex;gap:18px;font-size:.9rem}
.wrap{display:flex;max-width:1200px;margin:0 auto;min-height:calc(100vh - 60px)}
nav{width:250px;flex-shrink:0;padding:26px 10px 26px 22px;border-right:1px solid var(--edge)}
nav a{display:block;padding:7px 12px;border-radius:8px;color:var(--text);font-size:.92rem}
nav a:hover{background:var(--edge);text-decoration:none}
nav a.on{background:rgba(0,128,240,.14);color:var(--accent2);font-weight:600}
main{flex:1;padding:34px 42px 70px;min-width:0}
main h1{color:#fff;font-size:1.7rem;margin:0 0 6px}
main .sub{color:var(--muted);margin:0 0 26px}
main h3{color:var(--gold);font-size:1.06rem;margin:30px 0 8px;letter-spacing:.2px}
main p,main li{color:var(--text)}
main b{color:#fff}
main code{background:var(--code);border:1px solid var(--edge);border-radius:5px;
padding:1px 6px;font-size:.86em;color:#9fd0ff}
main table{border-collapse:collapse;width:100%;margin:12px 0;font-size:.92rem}
main th,main td{border:1px solid var(--edge);padding:8px 10px;text-align:left;vertical-align:top}
main th{background:var(--panel);color:#fff}
main ul{padding-left:22px}
.foot{border-top:1px solid var(--edge);color:var(--muted);font-size:.85rem;
padding:18px 42px;text-align:center}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:22px}
.card{border:1px solid var(--edge);background:var(--panel);border-radius:12px;
padding:16px 18px;display:block;color:var(--text)}
.card:hover{border-color:var(--accent);text-decoration:none}
.card b{color:var(--accent2);display:block;margin-bottom:5px}
.card span{font-size:.86rem;color:var(--muted)}
@media(max-width:900px){.wrap{flex-direction:column}
nav{width:100%;border-right:0;border-bottom:1px solid var(--edge);
display:flex;flex-wrap:wrap;gap:2px;padding:12px}
main{padding:24px 20px 50px}.cards{grid-template-columns:1fr}}
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - IdPVault Docs</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}">
<link rel="icon" type="image/png" href="/favicon.png">
<meta property="og:title" content="{title} - IdPVault Docs">
<meta property="og:description" content="{desc}">
<meta property="og:type" content="article">
<style>{css}</style>
</head><body>
<header class="top">
<a href="https://idpvault.com"><img src="/logo.png" alt="IdPVault"></a>
<div class="t">Docs <span>· self-hosted IdP backup &amp; restore</span></div>
<div class="links"><a href="https://idpvault.com">idpvault.com</a>
<a href="https://idpvault.com/pricing">Pricing</a>
<a href="https://github.com/KelTech-Services/IdPVault">GitHub</a></div>
</header>
<div class="wrap">
<nav>{nav}</nav>
<main><h1>{title}</h1><p class="sub">{desc}</p>
{body}
</main>
</div>
<footer class="foot">IdPVault - self-hosted backup, drift detection &amp; restore for
Authentik, Okta, and Auth0 · <a href="https://idpvault.com">idpvault.com</a></footer>
</body></html>
"""


def clean(frag: str) -> str:
    """Strip the sync-note comment and rewrite in-app cross-links
    (onclick="openDoc('x')...") into plain static links."""
    frag = re.sub(r"<!--.*?-->", "", frag, flags=re.S)
    frag = re.sub(
        r'<a href="#" onclick="openDoc\(\'([a-z-]+)\'\);?return false;?">',
        r'<a href="/\1.html">', frag)
    return frag.strip()


def nav_html(active: str) -> str:
    on = ' class="on"'
    out = [f'<a href="/"{on if active == "index" else ""}>Overview</a>']
    for key, title, _ in TOPICS:
        cls = ' class="on"' if key == active else ""
        out.append(f'<a href="/{key}.html"{cls}>{title}</a>')
    return "\n".join(out)


def main() -> None:
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    # brand assets + custom domain
    shutil.copy(os.path.join(ROOT, "frontend", "IdPVault_light_logo.png"),
                os.path.join(OUT, "logo.png"))
    shutil.copy(os.path.join(ROOT, "frontend", "IdPVault_favicon.png"),
                os.path.join(OUT, "favicon.png"))
    with open(os.path.join(OUT, "CNAME"), "w") as f:
        f.write("docs.idpvault.com\n")

    urls = [f"{SITE}/"]
    for key, title, desc in TOPICS:
        with open(os.path.join(SRC, f"{key}.html")) as f:
            body = clean(f.read())
        page = PAGE.format(title=title, desc=desc, css=CSS, nav=nav_html(key),
                           body=body, canonical=f"{SITE}/{key}.html")
        with open(os.path.join(OUT, f"{key}.html"), "w") as f:
            f.write(page)
        urls.append(f"{SITE}/{key}.html")

    cards = "\n".join(
        f'<a class="card" href="/{k}.html"><b>{t}</b><span>{d}</span></a>'
        for k, t, d in TOPICS)
    intro = ('<p>IdPVault takes encrypted, versioned snapshots of your identity '
             'provider configuration - every app, flow, policy, group, user, and '
             'assignment in <b>Authentik</b>, <b>Okta</b>, and <b>Auth0</b> - shows '
             'exactly what changed, and restores selectively with a dry-run preview. '
             'These docs are the same ones built into the app.</p>'
             f'<div class="cards">{cards}</div>')
    page = PAGE.format(title="IdPVault Documentation",
                       desc="Self-hosted backup, drift detection, and restore for "
                            "Authentik, Okta, and Auth0.",
                       css=CSS, nav=nav_html("index"), body=intro,
                       canonical=f"{SITE}/")
    with open(os.path.join(OUT, "index.html"), "w") as f:
        f.write(page)

    with open(os.path.join(OUT, "sitemap.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(f"<url><loc>{u}</loc></url>" for u in urls)
                + "\n</urlset>\n")
    print(f"built {len(TOPICS) + 1} pages -> {OUT}")


if __name__ == "__main__":
    main()
