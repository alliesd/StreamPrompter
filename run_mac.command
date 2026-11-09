#!/bin/bash
# Starts Stream Prompter. The first run sets up a private Python environment in .venv (needs internet).
cd "$(dirname "$0")" || exit 1

if [ ! -f .venv/installed.ok ]; then
  echo "First run: setting up Stream Prompter..."
  if ! python3 -m venv .venv; then
    echo "Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again."
    read -r -p "Press Enter to close."
    exit 1
  fi
  .venv/bin/python -m pip install --upgrade pip
  if ! .venv/bin/python -m pip install -r requirements.txt; then
    echo "Installing packages failed. Check your internet connection and try again."
    read -r -p "Press Enter to close."
    exit 1
  fi
  touch .venv/installed.ok
fi

exec .venv/bin/python -m stream_prompter "$@"
