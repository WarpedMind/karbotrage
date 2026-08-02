#!/bin/bash
# Watchdog for the S5a/S5b arbitrage canary.
#
# Two jobs, and the first one exists because of a documented failure:
#
#   1. HEARTBEAT. The canary runs as its own systemd unit, so it does NOT
#      inherit karbot_runner.py's supervision or its Telegram alerting.
#      Restart=always covers a crash; it does not cover a hang, and a hung
#      canary looks exactly like a quiet market. This project has been bitten
#      by precisely that before -- karbot-disk-alert.sh, the watchdog built to
#      prevent a silent outage, was itself silently non-functional from Session
#      26 to Session 29. So something outside the process has to watch it.
#
#   2. CANDIDATES. The canary's whole purpose is to catch rare windows that a
#      single snapshot cannot see. A candidate that lands in a JSONL file
#      nobody opens is worth nothing. This alerts the moment one appears.
#
# Edge-triggered via a state file, like karbot-disk-alert.sh: alerts on the
# transition, not on every run, so a long outage produces one message rather
# than ninety-six a day.
#
# Secrets come from /etc/karbot/secrets/karbot.env (chmod 600, outside the repo
# directory) per CLAUDE.md's VPS rules. Nothing here echoes the token.
#
# Install:
#   sudo cp scripts/karbot-canary-alert.sh /usr/local/bin/
#   sudo chmod 755 /usr/local/bin/karbot-canary-alert.sh
#   echo '*/15 * * * * root /usr/local/bin/karbot-canary-alert.sh' \
#     | sudo tee /etc/cron.d/karbot-canary-alert

set -euo pipefail

ENV_FILE="/etc/karbot/secrets/karbot.env"
# Overridable so both alert paths can be exercised against a scratch file
# without touching real data. An untested watchdog is worse than none -- the
# whole reason this script exists is that karbot-disk-alert.sh was silently
# broken for three sessions -- so these MUST be exercised on install.
LOG_FILE="${CANARY_LOG:-/home/ubuntu/karbotrage_v1/logs/basket_candidates.jsonl}"
STATE_DIR="${CANARY_STATE_DIR:-/var/lib}"
HEARTBEAT_STATE="${STATE_DIR}/karbot-canary-heartbeat.state"
CANDIDATE_STATE="${STATE_DIR}/karbot-canary-candidates.state"

# The canary sweeps every 300s and a sweep takes ~50s. 20 minutes tolerates
# three missed cycles plus a restart before crying wolf.
STALE_SECONDS=1200

TOKEN=$(grep -oP '(?<=^TELEGRAM_BOT_TOKEN=).*' "$ENV_FILE" || true)
CHAT_ID=$(grep -oP '(?<=^TELEGRAM_CHAT_ID=).*' "$ENV_FILE" || true)

send_alert() {
    local msg="$1"
    if [ -n "$TOKEN" ] && [ -n "$CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            -d text="${msg}" > /dev/null
    fi
}

# ---- 1. heartbeat -----------------------------------------------------------
# The canary writes a sweep record every cycle whether or not it finds
# anything, so the log's mtime IS the heartbeat.
LAST_HB="ok"
[ -f "$HEARTBEAT_STATE" ] && LAST_HB=$(cat "$HEARTBEAT_STATE")

if [ -f "$LOG_FILE" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$LOG_FILE") ))
else
    AGE=999999
fi

if [ "$AGE" -ge "$STALE_SECONDS" ] && [ "$LAST_HB" = "ok" ]; then
    send_alert "CANARY STALLED: no sweep written for $((AGE / 60)) minutes (threshold $((STALE_SECONDS / 60))). The S5a/S5b canary is not sweeping. Check: systemctl status karbot-canary; journalctl -u karbot-canary -n 50. This does NOT affect trading — the canary is detect-and-log only."
    echo "stalled" > "$HEARTBEAT_STATE"
elif [ "$AGE" -lt "$STALE_SECONDS" ] && [ "$LAST_HB" = "stalled" ]; then
    send_alert "Canary recovered: sweeping again (last write $((AGE / 60))m ago)."
    echo "ok" > "$HEARTBEAT_STATE"
fi

# ---- 2. candidates ----------------------------------------------------------
# Count only real candidate records. Deliberately matches the emitting
# identifier rather than a value that could appear anywhere: a naive grep for
# the word "candidate" also matches every sweep record's "candidates" field,
# and a naive grep for "429" once matched sequence numbers containing those
# digits and produced a false alarm.
COUNT=0
if [ -f "$LOG_FILE" ]; then
    COUNT=$(grep -c '"record": "candidate"' "$LOG_FILE" || true)
fi

LAST_COUNT=0
[ -f "$CANDIDATE_STATE" ] && LAST_COUNT=$(cat "$CANDIDATE_STATE")

if [ "$COUNT" -gt "$LAST_COUNT" ]; then
    NEW=$(( COUNT - LAST_COUNT ))
    DETAIL=$(grep '"record": "candidate"' "$LOG_FILE" | tail -1 \
        | python3 -c 'import json,sys; c=json.load(sys.stdin); e=c["economics"]; print("%s %s legs=%d cost/set=$%.4f payout=$%.2f size=%d net/set=$%+.4f status=%s" % (c["kind"], c["event_ticker"], c["n_legs"], e["cost_per_set"], e["payout_per_set"], e["max_contracts"], e["net_per_set_at_max"], c["status"]))' 2>/dev/null || echo "(could not parse latest record)")
    send_alert "CANARY FOUND ${NEW} ARBITRAGE CANDIDATE(S) — total ${COUNT}. Latest: ${DETAIL}. Detect-and-log only, nothing was traded. Check status=confirmed (survived an order-book re-check) vs vanished_on_recheck (existed only in a stale snapshot)."
fi
echo "$COUNT" > "$CANDIDATE_STATE"
