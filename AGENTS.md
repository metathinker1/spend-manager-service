# AGENTS.md

Guidelines for AI coding assistants working on this codebase.

## Project Overview

**spend-manager-service** is a Python service for processing financial transaction data, specifically parsing MoneyWell application exports (TSV format).

- **Python version**: 3.13+
- **Package manager**: UV (`uv` commands, not `pip`)
- **Main package**: `spend_manager_service/`

## Development Environment

### Setup Commands
```bash
uv sync              # Install dependencies
uv run python ...    # Run Python with project dependencies
```

### Key Dependencies
- **Pydantic v2**: Data validation and serialization
- **Pandas**: Data manipulation (available but not yet heavily used)

## Code Conventions

### Pydantic Models
- Define models in `spend_manager_service/models/`
- Use `Field(alias="...")` for external data mapping
- Prefer explicit type hints on all fields

```python
from pydantic import BaseModel, Field

class ExampleModel(BaseModel):
    field_name: str = Field(alias="external_name")
```

### Type Hints
- Use specific generic types: `list[MoneyWellTransaction]` not `List`
- Python 3.13+ means use built-in generics (`list`, `dict`) not `typing` imports

### File Structure
```
spend_manager_service/
├── models/           # Pydantic data models
└── (future modules)
```

## Testing

No test framework is currently configured. When adding tests:
- Use `pytest`
- Place tests in `tests/` directory
- Name test files `test_*.py`
- Add pytest to dev dependencies in pyproject.toml

## Common Tasks

### Adding a new data model
1. Create or edit file in `spend_manager_service/models/`
2. Import and re-export in `models/__init__.py` if needed
3. Use Pydantic BaseModel with Field definitions

### Adding dependencies
```bash
uv add <package>           # Runtime dependency
uv add --dev <package>     # Development dependency
```

## Known Issues / Technical Debt

- `moneywell_transaction.py:48` - `__int__()` should be `__init__()`
- No error handling for file I/O operations
- README file referenced as "READMME.md" (typo) and doesn't exist
- Pandas imported but unused in current implementation

## What NOT to Do

- Don't use `pip` - use `uv` for all package operations
- Don't assume Python < 3.13 compatibility
- Don't add type imports from `typing` module for `list`, `dict`, `set` etc. (use builtins)
