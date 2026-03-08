#!/usr/bin/env python3
"""One-off script: sends system documentation email and exits."""
import os, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

gmail_user = "sgseaux@gmail.com"
gmail_pass = os.environ.get("GMAIL_APP_PASSWORD")
recipient = "sergiosar@gmail.com"

if not gmail_pass:
    print("! GMAIL_APP_PASSWORD not set")
    exit(1)

html = """\
<div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;background:#0f172a;color:#e2e8f0;padding:32px;border-radius:12px">

<h1 style="color:#f59e0b;margin-bottom:4px">Centinela v3 — Dragon Doble SMA</h1>
<p style="color:#64748b;font-size:12px;margin-bottom:24px">System Documentation &amp; Backup — March 2026</p>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">1. What is this?</h2>
<p>An automated portfolio management system based on the <b>Dragon Portfolio</b> concept with a <b>dual SMA filter</b>:</p>
<ul>
  <li><b>SMA200</b> — Monthly exposure filter: allocates between risky assets and SHY based on how many assets are above their 200-day moving average</li>
  <li><b>SMA50</b> — Intra-month exit signal: if any Equity or Hard Asset position drops below its 50-day moving average, it exits to SHY until recovery</li>
</ul>
<p><b>Blocks &amp; Target Weights:</b></p>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px;text-align:left">Block</th><th style="padding:8px;text-align:left">Weight</th><th style="padding:8px;text-align:left">Universe</th></tr>
  <tr><td style="padding:6px 8px">Equity</td><td style="padding:6px 8px">19%</td><td style="padding:6px 8px">SPY, QQQ, IWM, VGK, EEM, EWZ, EWY, EWP, EPOL</td></tr>
  <tr><td style="padding:6px 8px">Bonds</td><td style="padding:6px 8px">19%</td><td style="padding:6px 8px">TLT, IEF, TIP, LQD</td></tr>
  <tr><td style="padding:6px 8px">Hard Assets</td><td style="padding:6px 8px">19%</td><td style="padding:6px 8px">GLD, SLV, DBA, CPER, DBC, GDX</td></tr>
  <tr><td style="padding:6px 8px">Long Volatility</td><td style="padding:6px 8px">24%</td><td style="padding:6px 8px">BTAL (1x leverage)</td></tr>
  <tr><td style="padding:6px 8px">Gold</td><td style="padding:6px 8px">19%</td><td style="padding:6px 8px">GLD (fixed)</td></tr>
</table>
<p><b>Selection:</b> Each month, the top 3 assets by momentum (best 3-month return) are picked per block. Bonds and Gold are fixed.</p>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">2. Architecture</h2>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px;text-align:left">Component</th><th style="padding:8px;text-align:left">Description</th></tr>
  <tr><td style="padding:6px 8px;font-weight:bold;color:#06b6d4">dragon_sma200.py</td><td style="padding:6px 8px">Backtest engine. Generates DragonDobleSMA_Backtest.html with full historical performance</td></tr>
  <tr><td style="padding:6px 8px;font-weight:bold;color:#06b6d4">dragon_live.py</td><td style="padding:6px 8px">Live dashboard + email engine. Generates DragonDobleSMA_Live.html. Sends daily emails with NAV, performance, signals, portfolio</td></tr>
  <tr><td style="padding:6px 8px;font-weight:bold;color:#06b6d4">index.html</td><td style="padding:6px 8px">Landing page linking to both dashboards</td></tr>
  <tr><td style="padding:6px 8px;font-weight:bold;color:#06b6d4">last_rebal.json</td><td style="padding:6px 8px">Tracks last rebalance date and current positions</td></tr>
  <tr><td style="padding:6px 8px;font-weight:bold;color:#06b6d4">.github/workflows/update-daily.yml</td><td style="padding:6px 8px">GitHub Actions: automates daily runs at 2pm and 3pm NY</td></tr>
</table>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">3. Daily Schedule (Mon-Fri)</h2>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px">Time (NY)</th><th style="padding:8px">UTC (EDT/EST)</th><th style="padding:8px;text-align:left">What happens</th></tr>
  <tr><td style="padding:6px 8px;text-align:center;font-weight:bold">14:00</td><td style="padding:6px 8px;text-align:center">18:00 / 19:00</td><td style="padding:6px 8px"><b>Pre-Close Alert</b> — Runs dragon_live.py in ALERT_MODE. Sends email with intraday NAV, Today/MTD/YTD, and SMA50 signal status. No dashboard update, no commit.</td></tr>
  <tr><td style="padding:6px 8px;text-align:center;font-weight:bold">15:00</td><td style="padding:6px 8px;text-align:center">19:00 / 20:00</td><td style="padding:6px 8px"><b>Full Run</b> — Runs backtest + live dashboard. Sends complete post-close email with portfolio, benchmarks, trade blotter. Updates HTML files, commits, pushes. Vercel auto-deploys.</td></tr>
</table>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">4. Daily Routine — What to Do</h2>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px;text-align:left">Email Subject</th><th style="padding:8px;text-align:left">Action Required</th></tr>
  <tr><td style="padding:6px 8px"><span style="color:#ef4444">ACTION — SELL XXX → SHY</span></td><td style="padding:6px 8px">Sell XXX, buy SHY with proceeds. Execute MOC or next-day open. Shares in the email.</td></tr>
  <tr><td style="padding:6px 8px"><span style="color:#10b981">ACTION — BUY XXX ← SHY</span></td><td style="padding:6px 8px">Sell SHY, buy XXX. Same execution.</td></tr>
  <tr><td style="padding:6px 8px"><span style="color:#f59e0b">Rebalance [Month]</span></td><td style="padding:6px 8px">1st trading day of month. Adjust positions per trade blotter in email.</td></tr>
  <tr><td style="padding:6px 8px"><span style="color:#94a3b8">NAV $X | YTD +X%</span></td><td style="padding:6px 8px">No action. Daily status update.</td></tr>
  <tr><td style="padding:6px 8px"><span style="color:#f59e0b">PRE-CLOSE — SELL/BUY</span></td><td style="padding:6px 8px">2pm alert. Prepare to execute at close.</td></tr>
  <tr><td style="padding:6px 8px"><span style="color:#94a3b8">Pre-Close — No signals</span></td><td style="padding:6px 8px">No action needed. Relax.</td></tr>
</table>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">5. Infrastructure</h2>
<ul>
  <li><b>GitHub repo:</b> <a href="https://github.com/ssaravia25/Dragon-Doble-SMA" style="color:#06b6d4">github.com/ssaravia25/Dragon-Doble-SMA</a></li>
  <li><b>Live dashboard:</b> <a href="https://dragon-portfolio-liard.vercel.app" style="color:#06b6d4">dragon-portfolio-liard.vercel.app</a></li>
  <li><b>Compute:</b> GitHub Actions (free tier, ~45s per run)</li>
  <li><b>Hosting:</b> Vercel (auto-deploys on push to main)</li>
  <li><b>Email:</b> Gmail SMTP via sgseaux@gmail.com</li>
  <li><b>Data:</b> Yahoo Finance (yfinance library)</li>
</ul>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">6. Secrets &amp; Config</h2>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px;text-align:left">Secret</th><th style="padding:8px;text-align:left">Where</th><th style="padding:8px;text-align:left">What</th></tr>
  <tr><td style="padding:6px 8px">GMAIL_APP_PASSWORD</td><td style="padding:6px 8px">GitHub repo → Settings → Secrets</td><td style="padding:6px 8px">Google App Password for sgseaux@gmail.com</td></tr>
</table>
<p><b>To regenerate App Password:</b> <a href="https://myaccount.google.com/apppasswords" style="color:#06b6d4">myaccount.google.com/apppasswords</a> → generate new → update in GitHub Secrets</p>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">7. Email Recipients</h2>
<p>Configured in <code style="background:#334155;padding:2px 6px;border-radius:3px">dragon_live.py</code> lines 49-59 (EMAIL_RECIPIENTS list). All receive via BCC (hidden from each other).</p>
<ol style="font-size:13px">
  <li>sergiosar@gmail.com</li>
  <li>sergio@kobo.cl</li>
  <li>alvaro@kobo.cl</li>
  <li>ianmcharboe@gmail.com</li>
  <li>nanogarcia@gmail.com</li>
  <li>jcarrasco@zinvestments.cl</li>
  <li>thomasbertiez@gmail.com</li>
  <li>anremar@gmail.com</li>
</ol>
<p>To add/remove: edit the list in dragon_live.py, commit, push.</p>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">8. Manual Operations</h2>
<table style="width:100%;font-size:13px;background:#1e293b;border-radius:6px;border-collapse:collapse;color:#e2e8f0;margin-bottom:16px">
  <tr style="color:#64748b;font-size:10px;text-transform:uppercase"><th style="padding:8px;text-align:left">Task</th><th style="padding:8px;text-align:left">How</th></tr>
  <tr><td style="padding:6px 8px">Trigger manual run</td><td style="padding:6px 8px"><code style="background:#334155;padding:2px 4px;border-radius:3px">gh workflow run update-daily.yml --repo ssaravia25/Dragon-Doble-SMA</code><br>Or: GitHub → Actions tab → "Run workflow" button</td></tr>
  <tr><td style="padding:6px 8px">Run locally</td><td style="padding:6px 8px"><code style="background:#334155;padding:2px 4px;border-radius:3px">cd Dragon\ Portfolio && python3 dragon_live.py</code><br>(email won't send without GMAIL_APP_PASSWORD env var)</td></tr>
  <tr><td style="padding:6px 8px">Check run history</td><td style="padding:6px 8px"><code style="background:#334155;padding:2px 4px;border-radius:3px">gh run list --repo ssaravia25/Dragon-Doble-SMA</code></td></tr>
  <tr><td style="padding:6px 8px">View run logs</td><td style="padding:6px 8px"><code style="background:#334155;padding:2px 4px;border-radius:3px">gh run view [RUN_ID] --repo ssaravia25/Dragon-Doble-SMA --log</code></td></tr>
  <tr><td style="padding:6px 8px">Update Gmail password</td><td style="padding:6px 8px"><code style="background:#334155;padding:2px 4px;border-radius:3px">gh secret set GMAIL_APP_PASSWORD --repo ssaravia25/Dragon-Doble-SMA</code></td></tr>
</table>

<!-- ============================================ -->
<h2 style="color:#06b6d4;border-bottom:1px solid #334155;padding-bottom:8px">9. Key Technical Details</h2>
<ul style="font-size:13px">
  <li><b>Yahoo Finance end date:</b> yf.download uses exclusive end date → we use TOMORROW to include today's data</li>
  <li><b>Smart cache:</b> price_cache.json invalidates after market close (4pm ET) if today's data is missing</li>
  <li><b>GitHub Actions workflow</b> uses dual cron (EDT/EST) with NY hour check to handle daylight saving time</li>
  <li><b>Transaction costs:</b> 30bps per SMA50 exit/re-entry switch modeled in backtest</li>
  <li><b>BTC-USD:</b> treated as late joiner (data starts ~2014), uses fractional shares</li>
  <li><b>Initial capital:</b> $10,000 (adjustable in dragon_live.py line 45)</li>
</ul>

<div style="margin-top:24px;padding:16px;background:#334155;border-radius:8px;font-size:11px;color:#94a3b8">
  <p><b>SFinance-alicIA | Centinela v3 — Dragon Doble SMA</b></p>
  <p>System documentation generated March 2026. This email serves as a backup of the complete system architecture and operational procedures.</p>
</div>

</div>
"""

msg = MIMEMultipart("alternative")
msg["Subject"] = "Centinela v3 — System Documentation & Backup"
msg["From"] = gmail_user
msg["To"] = recipient
msg.attach(MIMEText(html, "html"))

try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_pass)
        server.sendmail(gmail_user, recipient, msg.as_string())
    print(f"Backup email sent to {recipient}")
except Exception as e:
    print(f"Email error: {e}")
