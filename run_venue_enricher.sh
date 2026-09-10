#!/bin/bash
cd "$(dirname "$0")"
.venv/bin/python venue_enricher.py >> venue_enricher_run.log 2>&1
