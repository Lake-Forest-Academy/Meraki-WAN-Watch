# Meraki-WAN-Watch
Slack alerts when any Cisco Meraki MX uplink goes down or comes back — including the backup WAN.

## Why

Meraki Dashboard only alerts when the *primary* uplink changes. If your secondary/backup WAN fails while the primary is healthy, Dashboard says nothing, and you find out the day you need it. This is a [long-standing gap](https://community.meraki.com/t5/Security-SD-WAN/No-way-to-know-when-the-secondary-uplink-goes-down/m-p/60455).

This script polls the Dashboard API on a schedule and posts to Slack whenever an uplink moves between healthy and unhealthy.

```
Meraki uplink DOWN
DOWN: Q2XX-XXXX-XXXX (primary) WAN1 ready -> failed (public IP 203.0.113.10)
```

## Features

- Watches every uplink (WAN1, WAN2, cellular) on every MX in the organization
- Covers both units of a warm-spare (HA) pair
- Alerts on **down** and **recovered**, once per change (no repeat spam)
- One API call per check, far under Meraki's rate limit
- No dependencies beyond `requests`; secrets come from environment variables, never the code

| Status | Meaning | Treated as |
|---|---|---|
| `active` | Carrying traffic | Healthy |
| `ready` | Standby backup, healthy | Healthy |
| `failed`, `not connected`, `connecting`, anything else | Problem | **Down** |

## Requirements

- Python 3.8+
- A Meraki Dashboard API key (read-only org admin is enough)
- A Slack incoming webhook (optional — without one, alerts print to the console)
- A machine that's always on: Windows server, Linux VM, etc.

---

## Setup

### 1. Create a Slack incoming webhook

1. Go to <https://api.slack.com/apps> → **Create New App** → **Blank app**.
2. Name it (e.g. *Meraki Alerts*) and choose your workspace.
3. Under **Features → Incoming Webhooks**, switch **Activate Incoming Webhooks** on.
4. Click **Add New Webhook**, pick a channel, click **Allow**.
5. Copy the URL (`https://hooks.slack.com/services/...`).

> Treat the webhook URL like a password — anyone who has it can post to your channel. Renaming the channel later does not break it.

### 2. Get a Meraki API key and org ID

1. Dashboard → your name (top right) → **My profile** → **API access** → **Generate new API key**. Copy it; it's shown once.
   - If API access is missing: **Organization → Settings** → enable Dashboard API access.
   - Best practice: create a dedicated **read-only** org admin account for this key.
2. Find your org ID:

   **PowerShell** (the parentheses matter on Windows PowerShell 5.1):
   ```powershell
   $key = 'YOUR_KEY'
   (Invoke-RestMethod -Uri 'https://api.meraki.com/api/v1/organizations' -Headers @{ Authorization = "Bearer $key" }) | Select-Object id, name
   ```
   **Bash:**
   ```bash
   curl -s -H "Authorization: Bearer YOUR_KEY" https://api.meraki.com/api/v1/organizations
   ```

### 3. Install

```bash
git clone https://github.com/<you>/meraki-wan-watch.git
cd meraki-wan-watch
python -m pip install -r requirements.txt
```

### 4. Test by hand

Set the variables for the current session:

**PowerShell**
```powershell
$env:MERAKI_API_KEY    = 'your-key'
$env:MERAKI_ORG_ID     = 'your-org-id'
$env:SLACK_WEBHOOK_URL = 'https://hooks.slack.com/services/...'
```
**Bash**
```bash
export MERAKI_API_KEY='your-key'
export MERAKI_ORG_ID='your-org-id'
export SLACK_WEBHOOK_URL='https://hooks.slack.com/services/...'
```

Then:
```bash
python meraki_wan_watch.py --test     # posts a test message to Slack
python meraki_wan_watch.py --status   # lists every uplink and its status
python meraki_wan_watch.py            # first real run: records a baseline, sends nothing
```

The first normal run only records current state to `wan_state.json`. Alerts start from the second run.

### 5. Schedule it

#### Windows (Task Scheduler)

Easy way — from an **elevated** PowerShell window in the repo folder:
```powershell
.\scripts\install-windows-task.ps1 -ScriptPath "$PWD\meraki_wan_watch.py" -IntervalMinutes 5
```
It prompts for your key, org ID, and webhook (hidden input), saves them as machine environment variables, installs `requests`, and creates a task that runs every 5 minutes and at startup as SYSTEM. If PowerShell blocks the script, run `Set-ExecutionPolicy -Scope Process Bypass` first.

<details>
<summary>Manual way</summary>

```powershell
[Environment]::SetEnvironmentVariable('MERAKI_API_KEY', 'your-key', 'Machine')
[Environment]::SetEnvironmentVariable('MERAKI_ORG_ID', 'your-org-id', 'Machine')
[Environment]::SetEnvironmentVariable('SLACK_WEBHOOK_URL', 'https://hooks.slack.com/services/...', 'Machine')

$py        = (Get-Command python).Source
$script    = 'C:\Scripts\meraki-wan-watch\meraki_wan_watch.py'
$action    = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory (Split-Path $script)
$t1        = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)
$t2        = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName 'Meraki WAN Watch' -Action $action -Trigger $t1, $t2 -Principal $principal
```
</details>

Check it:
```powershell
Get-ScheduledTaskInfo -TaskName 'Meraki WAN Watch' | Select-Object LastRunTime, LastTaskResult, NextRunTime
```
`LastTaskResult` of `0` means success.

> Install Python **for all users** with **Add to PATH** checked, so the SYSTEM account can find it.

#### Linux (cron)

Put the secrets in a file only you can read:
```bash
sudo tee /etc/meraki-wan-watch.env >/dev/null <<'EOF'
MERAKI_API_KEY=your-key
MERAKI_ORG_ID=your-org-id
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
EOF
sudo chmod 600 /etc/meraki-wan-watch.env
```
Then `crontab -e`:
```
*/5 * * * * set -a; . /etc/meraki-wan-watch.env; set +a; /usr/bin/python3 /opt/meraki-wan-watch/meraki_wan_watch.py >> /var/log/meraki-wan-watch.log 2>&1
```

### 6. Prove it end to end

During a quiet window, unplug your backup WAN cable. Within one check interval (plus a minute or so for Meraki to notice), you should get a **DOWN** alert. Plug it back in and you should get **recovered**.

To test without touching cables: edit `wan_state.json`, change one `"ready"` to `"failed"`, and run the script. It will post a **recovered** message.

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `MERAKI_API_KEY` | Yes | Dashboard API key |
| `MERAKI_ORG_ID` | Yes | Organization ID |
| `SLACK_WEBHOOK_URL` | No | Slack incoming webhook. Unset = print to console |
| `STATE_FILE` | No | Path to the state file (default: `wan_state.json` beside the script) |

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `HTTP 401` | Bad API key — often a stray character from pasting. Keys are 40 hex characters |
| `HTTP 404` | Wrong org ID, or the key's account can't see that org |
| Blank table when listing orgs in PowerShell | Wrap the `Invoke-RestMethod` call in parentheses |
| `can't open file` | Wrong script path, or Windows saved it as `.py.txt` |
| Task runs but `LastTaskResult` isn't `0` | Variables weren't saved at Machine scope, or `requests` isn't installed for all users |
| No alert for a very short blip | It happened between checks. Shorten the interval (1 minute is fine) |

## Limitations

- **Polling, not push.** Outages shorter than the check interval can be missed.
- **Fails quietly.** If the key is revoked or the host is down, the script exits non-zero but can't tell Slack. Watch your scheduler's last-run result.
- **Health = status only.** A link that's `ready` but dropping packets isn't flagged. Meraki's loss/latency endpoints could extend this.

## Security

- Never commit your API key or webhook URL. `.gitignore` excludes `wan_state.json` and `.env` files.
- If a key or webhook leaks (screenshots count), rotate it: Meraki allows two active keys, so add the new one before revoking the old.

## License

MIT — see [LICENSE](LICENSE).
