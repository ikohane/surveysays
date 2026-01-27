# Test Scripts

Automated test suite for SurveySays Admin App.

## Test Files

### `test_schema_conformance.py`

**Purpose**: Validates that all three schema definitions are structurally consistent.

**What it tests**:
- SQLite schema (`SCHEMA_SQL` in `db.py`)
- PostgreSQL schema (`SCHEMA_SQL_POSTGRES` in `db.py`)
- Cloudflare D1 schema (`cloudflare/pages/schema.sql`)

**What it catches**:
- Missing tables in any environment
- Column mismatches (name, type, nullability)
- Missing indexes
- Type mapping inconsistencies
- Drift between local and cloud schemas

**Usage**:
```bash
python3 admin_app/scripts/test_schema_conformance.py
```

**Exit codes**:
- `0`: All schemas match
- `1`: Schema inconsistencies found

**See**: [docs/SCHEMA_CONFORMANCE.md](../../docs/SCHEMA_CONFORMANCE.md)

---

### `integration_test.py`

**Purpose**: End-to-end functional testing of the admin app.

**What it tests**:
- Campaign creation (all three strategies)
- Data import (cases, recipients, templates)
- Variant generation
- Question bank population (online_assign)
- Token creation
- Survey rendering
- Submission handling
- Cloud mode sync
- Layout YAML editing

**Usage**:
```bash
python3 admin_app/scripts/integration_test.py
```

**Requirements**: None (creates isolated test database)

---

### `test_wave_workflow.py`

**Purpose**: Tests the wave-based generation workflow.

**What it tests**:
- Wave 1 generation
- Wave 2 additive generation
- Recipient exclusions
- Wave tracking

**Usage**:
```bash
python3 admin_app/scripts/test_wave_workflow.py
```

---

### `ui_smoke_test.py`

**Purpose**: Basic UI smoke test.

**What it tests**:
- Homepage loads
- Campaign pages render
- Master view accessible

**Usage**:
```bash
# Server must be running on localhost:5055
python3 admin_app/scripts/ui_smoke_test.py
```

**Requirements**: Flask server running

---

### `run_all_tests.py`

**Purpose**: Runs the complete test suite in order.

**Test order**:
1. Schema conformance (critical)
2. Integration tests (functional)
3. UI smoke tests (if server running)

**Usage**:
```bash
python3 admin_app/scripts/run_all_tests.py
```

**Exit codes**:
- `0`: All tests passed
- `1`: At least one test failed

**Recommended**: Run before every commit and deploy.

---

## Running Tests

### Quick Check (Schema + Integration)

```bash
python3 admin_app/scripts/run_all_tests.py
```

### Full Suite (with UI)

```bash
# Terminal 1: Start server
python3 restart.py

# Terminal 2: Run tests
python3 admin_app/scripts/run_all_tests.py
```

### Individual Tests

```bash
# Schema only (fast, no dependencies)
python3 admin_app/scripts/test_schema_conformance.py

# Integration only (slower, comprehensive)
python3 admin_app/scripts/integration_test.py

# UI only (requires server)
python3 admin_app/scripts/ui_smoke_test.py
```

## CI/CD Integration

```yaml
# GitHub Actions example
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -e ./qgen
          pip install -e ./admin_app
      - name: Run all tests
        run: python3 admin_app/scripts/run_all_tests.py
```

## Test Development Guidelines

### Adding New Tests

1. **Start with schema** - If adding tables/columns, update schema conformance first
2. **Add integration test** - Test the full workflow end-to-end
3. **Update run_all_tests.py** - Include new test in the suite
4. **Document** - Add entry to this README

### Test Naming

- `test_*.py` - Automated test scripts
- `*_test.py` - Also acceptable
- `integration_test.py` - End-to-end tests
- `ui_*_test.py` - UI-specific tests

### Test Isolation

- Use separate test databases (`out/integration_test.sqlite3`)
- Clean up after tests (delete test DB)
- Don't rely on global state
- Mock external services (Cloudflare, Resend)

## Troubleshooting

### "ModuleNotFoundError: No module named 'qgen'"

```bash
pip install -e ./qgen
pip install -e ./admin_app
```

### "Schema conformance test failed"

See [docs/SCHEMA_CONFORMANCE.md](../../docs/SCHEMA_CONFORMANCE.md) for details on fixing schema issues.

### "UI smoke test failed"

Make sure server is running:
```bash
python3 restart.py
```

### Tests pass locally but fail in CI

- Check Python version (requires 3.11+)
- Verify all dependencies installed
- Check for environment-specific paths
- Review CI logs for detailed errors

## Future Improvements

- [ ] Add performance benchmarks
- [ ] Test Railway API endpoints
- [ ] Add Cloudflare D1 integration tests
- [ ] Test email sending (mock Resend API)
- [ ] Add test coverage reporting
- [ ] Parallel test execution
