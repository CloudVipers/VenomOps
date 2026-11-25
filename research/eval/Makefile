# research/eval — lint / test / build del arnés de evaluación (ADR 0011).
PY := .venv/bin
PKGS := ../../packages

.PHONY: setup lint test build e4

# Crea el venv con el contrato común y los dos paquetes Python que se evalúan, en modo editable.
setup:
	@if [ ! -x $(PY)/python ]; then \
		if command -v uv >/dev/null 2>&1; then \
			uv venv -q .venv && uv pip install -q --python $(PY)/python -e $(PKGS)/findings-schema/python \
				-e $(PKGS)/pr-agent -e $(PKGS)/arch-committee -e ".[dev]"; \
		else \
			python3 -m venv .venv && $(PY)/pip install -q -e $(PKGS)/findings-schema/python \
				-e $(PKGS)/pr-agent -e $(PKGS)/arch-committee -e ".[dev]"; \
		fi; \
	fi

lint: setup
	$(PY)/ruff check .
	$(PY)/ruff format --check .
	$(PY)/mypy src tests

# Solo pruebas deterministas: ninguna llama a Bedrock.
test: setup
	$(PY)/python -m pytest -q

build: setup
	$(PY)/python -m compileall -q src

# E4 (redacción) es determinista: se puede correr siempre, sin modelo.
e4: setup
	$(PY)/venom-eval e4
