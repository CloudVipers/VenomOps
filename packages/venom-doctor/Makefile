# venom-doctor — lint / test / build.
BIN := bin/kubectl-venom_doctor
VERSION ?= dev

.PHONY: lint test build snapshot

lint:
	@test -z "$$(gofmt -l .)" || { echo "gofmt: archivos sin formatear:"; gofmt -l .; exit 1; }
	go vet ./...
	@if command -v golangci-lint >/dev/null 2>&1; then golangci-lint run ./...; else echo "golangci-lint no instalado: se omite (CI lo ejecuta)"; fi

test:
	go test -race -cover ./...

build:
	go build -ldflags "-X main.version=$(VERSION)" -o $(BIN) ./cmd/kubectl-venom_doctor

# Compila los binarios de release sin publicar nada (requiere goreleaser).
snapshot:
	goreleaser build --snapshot --clean --config .goreleaser.yaml
