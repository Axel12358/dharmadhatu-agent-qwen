#!/bin/bash
cd "$(dirname "$0")"
.venv/bin/python run_orquestador.py >> orquestador_run.log 2>&1
