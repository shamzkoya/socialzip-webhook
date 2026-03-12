#!/usr/bin/env bash
# ============================================================
# Run: Pamphlet → WooCommerce Import Tool
# ============================================================
# Usage:
#   bash run_pamphlet.sh --input ./pamphlet_images --markup 40
#   bash run_pamphlet.sh --input catalogue.pdf --markup 35 --vat 15
#   bash run_pamphlet.sh --input ./images --markup 40 --output my_products.csv
#   bash run_pamphlet.sh --input ./images --markup 40 --no-images   (skip image search)
#   bash run_pamphlet.sh --input ./images --markup 40 --no-seo      (skip SEO generation)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv_pamphlet"
SCRIPT="$SCRIPT_DIR/pamphlet_to_woocommerce.py"

# Check virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "Virtual environment not found. Run setup first:"
    echo "  bash $SCRIPT_DIR/setup_pamphlet.sh"
    echo ""
    exit 1
fi

# Check .env exists
ENV_FILE="$SCRIPT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    echo ""
    echo "WARNING: .env file not found."
    echo "  Create it: cp $SCRIPT_DIR/.env.example $ENV_FILE"
    echo "  Then add:  ANTHROPIC_API_KEY=sk-ant-..."
    echo ""
fi

# Activate venv and run
source "$VENV_DIR/bin/activate"
python3 "$SCRIPT" "$@"
