# Contributing to DeDox

Thank you for your interest in contributing to DeDox! This document provides guidelines and instructions for contributing.

## Development Setup

### Prerequisites

- Python 3.10 or higher
- Docker and Docker Compose
- Tesseract OCR (for local development)
- Git

### Local Development

1. **Clone the repository**:
   ```bash
   git clone https://github.com/bytecube/DeDox.git
   cd DeDox
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Copy environment configuration**:
   ```bash
   cp .env.example .env
   # Edit .env with your local settings
   ```

5. **Run the development server**:
   ```bash
   uvicorn dedox.api.app:app --reload --port 8000
   ```

### Docker Development

For a complete local environment including Paperless-ngx and Ollama:

```bash
docker-compose up -d
```

For a minimal setup (DeDox + Ollama only, requires external Paperless):

```bash
docker-compose -f docker-compose.minimal.yml up -d
```

## Code Style

### Python Style Guidelines

- **Type hints**: Use Python 3.10+ style type hints (`str | None` instead of `Optional[str]`)
- **Docstrings**: Use Google-style docstrings for modules, classes, and functions
- **Line length**: Maximum 100 characters
- **Imports**: Group imports in order: standard library, third-party, local

### Example Code Style

```python
"""Module docstring describing the purpose."""

import logging
from datetime import datetime

import httpx

from dedox.core.config import get_settings

logger = logging.getLogger(__name__)


class ExampleService:
    """Service for handling example operations.

    Attributes:
        client: HTTP client for API requests
    """

    def __init__(self) -> None:
        """Initialize the service."""
        self.client = None

    async def process_item(
        self,
        item_id: str,
        options: dict | None = None,
    ) -> dict:
        """Process an item with the given options.

        Args:
            item_id: Unique identifier for the item
            options: Optional processing configuration

        Returns:
            Processing result with status and data

        Raises:
            ValueError: If item_id is invalid
        """
        if not item_id:
            raise ValueError("item_id is required")

        return {"status": "ok", "item_id": item_id}
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_api.py

# Run with coverage
pytest --cov=dedox --cov-report=html
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files with `test_` prefix
- Name test classes with `Test` prefix
- Name test functions with `test_` prefix
- Use fixtures from `conftest.py` for common setup

Example test:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

class TestMyFeature:
    """Tests for MyFeature."""

    @pytest.mark.asyncio
    async def test_process_returns_result(self, mock_settings):
        """Test that process returns expected result."""
        service = MyService()
        result = await service.process("test-id")

        assert result["status"] == "ok"
        assert "data" in result
```

### Test Coverage

We aim for meaningful test coverage, focusing on:
- Business logic and services
- API endpoints
- Pipeline processors
- Error handling

## Pull Request Process

### Before Submitting

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the code style guidelines

3. **Run tests** and ensure they pass:
   ```bash
   pytest
   ```

4. **Update documentation** if needed

### Submitting

1. **Push your branch**:
   ```bash
   git push -u origin feature/your-feature-name
   ```

2. **Create a Pull Request** with:
   - Clear title describing the change
   - Description of what was changed and why
   - Reference to any related issues

3. **Wait for review** and address any feedback

### Commit Messages

Use clear, descriptive commit messages:

- `feat: Add document batch upload endpoint`
- `fix: Resolve OCR confidence calculation`
- `docs: Update API documentation`
- `test: Add tests for search functionality`
- `refactor: Simplify pipeline orchestrator`

## Architecture Overview

DeDox follows a layered architecture:

```
API Layer (FastAPI)
    ↓
Service Layer (Business Logic)
    ↓
Pipeline Layer (Document Processing)
    ↓
Data Layer (SQLite + Repositories)
```

Key components:
- **API**: FastAPI routes in `dedox/api/routes/`
- **Services**: Business logic in `dedox/services/`
- **Pipeline**: Document processors in `dedox/pipeline/processors/`
- **Database**: Repositories in `dedox/db/repositories/`

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Getting Help

- **Issues**: Open an issue for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
