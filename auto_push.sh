#!/usr/bin/env bash
cd ~/us_stock_screener || exit 1
git add -A
if ! git diff --cached --quiet; then
  git commit -m "auto: $(date -u +%F_%H:%M)" && git push origin main
fi
