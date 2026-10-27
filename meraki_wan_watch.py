#!/usr/bin/env python3
"""
meraki_wan_watch.py - Slack alerts when any Meraki MX uplink (including the backup) goes down or recovers.

Meraki Dashboard only alerts when the *primary* uplink changes. If your backup/secondary
WAN fails, you hear nothing until you need it. This script polls the Dashboard API and
posts to Slack whenever any uplink changes between healthy and unhealthy.

Healthy states:  active (carrying traffic), ready (standby backup, healthy)
Alert states:    failed, not connected, connecting, anything else

Configuration (environment variables):
  MERAKI_API_KEY       required  Dashboard API key (read-only access is enough)
  MERAKI_ORG_ID        required  Organization ID
  SLACK_WEBHOOK_URL    optional  Slack incoming webhook; if unset, alerts print to the console
  STATE_FILE           optional  Where to keep last-known state (default: wan_state.json next to this script)

Usage:
  python meraki_wan_watch.py           # normal check (run on a schedule)
  python meraki_wan_watch.py --status  # print current uplink status, change nothing
  python meraki_wan_watch.py --test    # send a test message to Slack
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

API = "https://api.meraki.com/api/v1"
HEALTHY = {"active", "ready"}


def env(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def webhook_url():
    # TEAMS_OR_SLACK_WEBHOOK is accepted for backward compatibility with earlier versions.
    return env("SLACK_WEBHOOK_URL", False) or env("TEAMS_OR_SLACK_WEBHOOK", False)


def get_uplinks(api_key, org_id):
    """Yield (device label, interface, status, public IP) for every MX uplink in the org."""
    url = f"{API}/organizations/{org_id}/appliance/uplink/statuses"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"perPage": 1000}
    while url:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        for dev in r.json():
            label = dev.get("serial", "unknown")
            ha = dev.get("highAvailability") or {}
            if ha.get("enabled"):
                label += f" ({ha.get('role')})"
            for up in dev.get("uplinks", []):
                yield label, up.get("interface", "?"), up.get("status", "unknown"), up.get("publicIp")
        url = r.links.get("next", {}).get("url")  # follow pagination
        params = None


def notify(subject, body):
    hook = webhook_url()
    if not hook:
        print(f"{subject}\n{body}")
        return
    r = requests.post(hook, json={"text": f"*{subject}*\n{body}"}, timeout=15)
    r.raise_for_status()


def load_state(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def check(api_key, org_id, state_file):
    old = load_state(state_file)
    new, changes = {}, []

    for label, iface, status, ip in get_uplinks(api_key, org_id):
        key = f"{label}|{iface}"
        new[key] = status
        was = old.get(key)
        if was is None:
            continue  # first time seeing this uplink: record a baseline, don't alert
        if (was in HEALTHY) != (status in HEALTHY):
            state = "RECOVERED" if status in HEALTHY else "DOWN"
            changes.append(f"{state}: {label} {iface.upper()} {was} -> {status} (public IP {ip})")

    state_file.write_text(json.dumps(new, indent=2))

    if changes:
        down = any(c.startswith("DOWN") for c in changes)
        notify(f"Meraki uplink {'DOWN' if down else 'recovered'}", "\n".join(changes))


def main():
    state_file = Path(env("STATE_FILE", False) or Path(__file__).with_name("wan_state.json"))
    arg = sys.argv[1] if len(sys.argv) > 1 else ""

    if arg == "--test":
        notify("Meraki WAN Watch test", f"Test message sent {datetime.now():%Y-%m-%d %H:%M}. Alerts will post here.")
        print("Test message sent.")
        return

    api_key, org_id = env("MERAKI_API_KEY"), env("MERAKI_ORG_ID")

    if arg == "--status":
        for label, iface, status, ip in get_uplinks(api_key, org_id):
            mark = "OK  " if status in HEALTHY else "DOWN"
            print(f"[{mark}] {label:32} {iface:6} {status:15} {ip or ''}")
        return

    check(api_key, org_id, state_file)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        hint = " (check MERAKI_API_KEY)" if code == 401 else " (check MERAKI_ORG_ID / key access)" if code == 404 else ""
        print(f"meraki_wan_watch: HTTP {code}{hint}: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"meraki_wan_watch error: {e}", file=sys.stderr)
        sys.exit(1)
