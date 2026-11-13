#!/usr/bin/env bash
# Run once a day (cron: 15 6 * * * /path/to/fl-property-assistant/daily.sh >> /path/to/daily.log 2>&1)
cd "$(dirname "$0")"
exec .venv/bin/python -m flprops daily "$@"
