@echo off
title Veda - Personal Offline AI Companion
cd /d "%~dp0"
set PYTHONPATH=src
.venv\Scripts\python -m controller.cli %*
