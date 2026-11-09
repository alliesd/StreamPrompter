#!/bin/bash
# Starts Stream Prompter. The first run sets up a private Python environment in .venv (needs internet).
# Needs: sudo apt install python3-venv python3-tk libportaudio2   (or your distro's equivalents)
cd "$(dirname "$0")" || exit 1

if [ ! -f .venv/installed.ok ]; then
  echo "First run: setting up Stream Prompter..."
  if ! python3 -m venv .venv; then
    echo "Couldn't create a virtual environment. Install python3-venv and try again."
    exit 1
  fi
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt || { echo "Installing packages failed."; exit 1; }
  touch .venv/installed.ok
fi

exec .venv/bin/python -m stream_prompter "$@"
