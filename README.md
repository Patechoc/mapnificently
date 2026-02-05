# Mapnificently

A Python implementation of [Mapnificent](https://www.mapnificent.net/) - a public transit accessibility visualization tool that shows areas reachable by public transport within a given time from any starting point.

## Features

- Parse GTFS (General Transit Feed Specification) data
- Calculate isochrones (areas reachable within a time limit)
- Generate GeoJSON output for visualization
- Pydantic models for type-safe data handling

## Installation

```bash
# Using uv (recommended)
uv sync

# With visualization extras
uv sync --extra viz

# Development installation
uv sync --all-extras
```

## Quick Start

```python
from mapnificently.models import IsochroneConfig
from datetime import time

# Configure isochrone calculation
config = IsochroneConfig(
    origin_lat=59.9110,
    origin_lon=10.7528,
    departure_time=time(8, 0, 0),
    max_travel_time_seconds=1800,  # 30 minutes
)

# TODO: Load GTFS data and calculate isochrone
```

## Development

```bash
# Install dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run linting
uv run ruff check .

# Run formatting
uv run ruff format .

# Run type checking
uv run mypy src/

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

## License

MIT
