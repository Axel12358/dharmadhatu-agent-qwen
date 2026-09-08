# Contributing to Dharmadhatu Agent

## Git Workflow

### Branching Strategy
```
main (production)
  └── develop (integration)
       └── feature/* (new features)
       └── experiment/* (research)
       └── bugfix/* (fixes)
```

### Branch Naming
- `feature/<name>`: New functionality
- `experiment/<name>`: Research/prototype
- `bugfix/<name>`: Bug fixes
- `hotfix/<name>`: Emergency production fixes

### Commit Messages
Use conventional commits:
```
feat(organizer): add Telegram channel crawling
fix(csv): prevent race condition in lock
docs(readme): update pipeline architecture
refactor(serp): optimize city sweep rotation
```

### PR Process
1. Create feature branch from `develop`
2. Make changes, commit frequently
3. Open PR against `develop`
4. Describe what changed, why, and how to test
5. Merge after validation

## Code Standards

### Python
- Follow PEP 8
- Use type hints where practical
- Keep functions focused (< 50 lines)
- Document non-obvious logic

### CSV Operations
**ALWAYS** use `csv_locked_rows()` for CSV access:
```python
from core.csv_lock import csv_locked_rows

with csv_locked_rows(CSV_FILE) as (rows, fieldnames):
    for r in rows:
        # modify r
```

Never write CSV without the lock. Race conditions cause data loss.

### Tor Constraint
All HTTP requests MUST go through Tor:
```python
from core.http_client import crear_sesion_tor
sesion = crear_sesion_tor()
response = sesion.get(url)
```

Never use `requests.get()` directly. Never expose real IP.

## Testing

### Before Commit
```bash
# Syntax check
python -m py_compile loop_completar.py
python -m py_compile core/csv_lock.py

# Run tests (if available)
pytest tests/
```

### Manual Validation
1. Check loop logs for errors
2. Verify CSV integrity (no empty writes)
3. Confirm Tor connectivity

## Data Management

### CSV Backups
- Loop creates `.bak_*` files before major operations
- Never delete backups manually
- Keep last 3 backups minimum

### JSON State Files
- `loop_completar_checkpoint.json`: Loop state
- `venue_cache.json`: Venue contacts
- `geo_cache.json`: City→country mappings
- These are runtime files, not code

## Common Pitfalls

1. **Race condition**: Multiple writers → use csv_lock
2. **Tor leak**: Direct requests → always use crear_sesion_tor
3. **Stale snapshot**: Writing without re-reading → csv_lock handles this
4. **Generic orgs**: "Events", "Party" → add to _PLACEHOLDER_ORGS
5. **Email pollution**: social domains → add to NO_DOMAINS

## Release Process

1. Feature complete on `feature/*`
2. Merge to `develop`
3. Test in staging (if available)
4. Merge to `main`
5. Tag with version: `git tag -a v1.0.0 -m "Description"`
