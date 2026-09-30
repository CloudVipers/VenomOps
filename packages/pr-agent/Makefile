# pr-agent — lint / test / build.
PY := .venv/bin

.PHONY: setup lint test build

# Crea el venv: primero el contrato común (findings-schema), luego pr-agent con sus dependencias de desarrollo.
setup:
	@if [ ! -x $(PY)/python ]; then \
		if command -v uv >/dev/null 2>&1; then \
			uv venv -q .venv && uv pip install -q --python $(PY)/python -e ../findings-schema/python -e ".[dev]"; \
		else \
			python3 -m venv .venv && $(PY)/pip install -q -e ../findings-schema/python -e ".[dev]"; \
		fi; \
	fi

lint: setup
	$(PY)/ruff check .
	$(PY)/ruff format --check .
	$(PY)/mypy src tests

test: setup
	$(PY)/python -m pytest -q

build: setup
	$(PY)/python -m compileall -q src
