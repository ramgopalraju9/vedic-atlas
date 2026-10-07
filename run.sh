#!/bin/bash
cd "$(dirname "$0")"
PYTHONPATH=src exec python3 -m controller.cli "$@"
