#!/usr/bin/env bash
# One-command setup for macOS / Linux: virtual environment, all packages, database and editable config.
set -e
cd "$(dirname "$0")"
python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[all]"
tripmoney init
echo
echo "Installed. Start the app with:  ./start.sh"
