@echo off
title Veda - Personal Offline AI Companion
cd /d "%~dp0"
set PYTHONPATH=src
vedic-atlas-env\Scripts\python -m controller.cli %*
