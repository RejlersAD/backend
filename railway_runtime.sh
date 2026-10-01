#!/bin/bash

set -e

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
export PYTHONUNBUFFERED=1
export PORT="${PORT:-8000}"

# One parent forwards Railway shutdown signals and monitors all enabled children.
exec python railway_runtime.py
