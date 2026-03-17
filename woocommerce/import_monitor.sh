#!/bin/bash
# Polls import progress every 5 minutes and prints a summary line
while true; do
  PROG=$(cat /home/user/socialzip-webhook/woocommerce/stock_control/import_progress.json 2>/dev/null)
  ERRS=$(python3 -c "import json,sys; e=json.load(open('/home/user/socialzip-webhook/woocommerce/stock_control/import_errors.json')); print(len(e))" 2>/dev/null || echo "?")
  TOTAL=$(curl -s -u 'ck_d5322bdf8c16388bb4adcc573d7a0417b5ad0938:cs_f75a2e8162382574310656a14cb368e823c3caf4' \
    'https://happyharvesting.co.za/wp-json/wc/v3/products?per_page=1' -o /dev/null -D - 2>/dev/null | grep -i X-WP-Total | awk '{print $2}' | tr -d '\r')
  echo "$(date '+%H:%M:%S')  progress=$PROG  errors=$ERRS  wc_total=$TOTAL"
  sleep 300
done
