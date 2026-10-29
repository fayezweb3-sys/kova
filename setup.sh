#!/bin/bash
# One-time setup on a Mac or Linux machine. Run from this folder:  bash setup.sh
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install -q -e ".[dev]"
python -m pytest -q
echo
echo "Installed. Next:"
echo "  source .venv/bin/activate"
echo "  python -m kova refresh        # download and load the real data (a few minutes)"
echo "  python -m kova verify         # check the Ontario rules against the e-Formulary"
echo "  python -m kova serve          # open http://127.0.0.1:8000"
