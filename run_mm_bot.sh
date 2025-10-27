#!/bin/bash
set -euo pipefail
source "$HOME/.bash_profile"
conda activate event_betting
python /Users/apurvgandhi/event_betting/market_making/main.py "$@"
