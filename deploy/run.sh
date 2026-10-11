#!/bin/sh
set -eu
umask 077
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
export SYNC_SCHEDULED=true
exec flock -n -E 0 .run.lock timeout --kill-after=10s 10m .venv/bin/python -u main.py
