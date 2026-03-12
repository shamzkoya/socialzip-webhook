#!/usr/bin/env bash
# ============================================================
# Setup: Pamphlet → WooCommerce Import Tool
# Run once on Ubuntu/WSL before using the tool
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "============================================================"
echo "  Setting up Pamphlet → WooCommerce Tool"
echo "============================================================"

# ── 1. Check Python ──────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install with: sudo apt-get install python3"
    exit 1
fi
PYTHON_VERSION=$(python3 --version)
echo "  Python: $PYTHON_VERSION"

# ── 2. Install system dependency: poppler (for PDF support) ──
echo ""
echo "  Installing poppler-utils (needed for PDF → image conversion)..."
if command -v apt-get &>/dev/null; then
    sudo apt-get install -y poppler-utils 2>/dev/null && echo "  poppler-utils installed" || echo "  (poppler-utils install skipped — may already be installed)"
else
    echo "  Skipping poppler install (not Ubuntu/Debian). Install manually if you need PDF support."
fi

# ── 3. Create virtual environment ────────────────────────────
VENV_DIR="$SCRIPT_DIR/venv_pamphlet"

if [ -d "$VENV_DIR" ]; then
    echo ""
    echo "  Virtual environment already exists at: $VENV_DIR"
    echo "  Updating packages..."
else
    echo ""
    echo "  Creating Python virtual environment at: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# ── 4. Install Python packages ────────────────────────────────
echo ""
echo "  Installing Python packages..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements_pamphlet.txt"

echo ""
echo "  Packages installed:"
"$VENV_DIR/bin/pip" list | grep -E "anthropic|requests|duckduckgo|Pillow|pdf2image"

# ── 5. Set up .env file ───────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        echo ""
        echo "  Created .env file from .env.example"
        echo "  *** IMPORTANT: Edit .env and add your Anthropic API key ***"
        echo "      nano $ENV_FILE"
    fi
else
    echo ""
    echo "  .env file already exists"
fi

echo ""
echo "============================================================"
echo "  Setup complete!"
echo "============================================================"
echo ""
echo "  NEXT STEPS:"
echo "  1. Add your API key to .env:"
echo "       nano $ENV_FILE"
echo "       → set ANTHROPIC_API_KEY=sk-ant-..."
echo ""
echo "  2. Put your pamphlet images in a folder (e.g. ~/pamphlets/)"
echo ""
echo "  3. Run the tool:"
echo "       bash run_pamphlet.sh --input ~/pamphlets --markup 40"
echo ""
echo "  For PDF pamphlets:"
echo "       bash run_pamphlet.sh --input ~/pamphlet.pdf --markup 40"
echo ""
