# findings-schema — lint / test / build (Go + Python).
PY := python/.venv/bin

.PHONY: setup lint test build

# Crea el venv de Python con las dependencias de desarrollo (usa uv si está instalado).
setup:
	@if [ ! -x $(PY)/python ]; then \
		if command -v uv >/dev/null 2>&1; then \
			uv venv -q python/.venv && uv pip install -q --python $(PY)/python -e "python[dev]"; \
		else \
			python3 -m venv python/.venv && $(PY)/pip install -q -e "python[dev]"; \
		fi; \
	fi

lint: setup
	@test -z "$$(gofmt -l .)" || { echo "gofmt: archivos sin formatear:"; gofmt -l .; exit 1; }
	go vet ./...
	cd python && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy findings_schema tests

test: setup
	go test -race -count=1 ./go/...
	$(PY)/python -m pytest python -q

build:
	go build ./...
