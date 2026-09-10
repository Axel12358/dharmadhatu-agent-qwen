#!/bin/bash
cd "$(dirname "$0")"
.venv/bin/python loop_completar.py >> loop_completar_run.log 2>&1
