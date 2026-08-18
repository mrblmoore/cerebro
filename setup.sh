#!/usr/bin/env bash
# Cerebro setup. Kept for muscle memory — it just calls the cross-platform installer.
cd "$(dirname "$0")"
exec ./cerebro.sh setup "$@"
