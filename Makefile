# VenomOps — atajos para todo el monorepo.
# Cada paquete en packages/ puede traer su propio Makefile con los targets lint/test/build;
# los paquetes que aún no lo tienen (Fase 0) simplemente se saltan.

PACKAGES := $(sort $(wildcard packages/*))

.PHONY: help lint test build

help:
	@echo "Targets: lint | test | build  (delegan a packages/*/Makefile)"

lint:
	@$(MAKE) --no-print-directory foreach TARGET=lint

test:
	@$(MAKE) --no-print-directory foreach TARGET=test

build:
	@$(MAKE) --no-print-directory foreach TARGET=build

.PHONY: foreach
foreach:
	@for pkg in $(PACKAGES); do \
		if [ -f "$$pkg/Makefile" ]; then \
			echo "==> $$pkg: $(TARGET)"; \
			$(MAKE) --no-print-directory -C "$$pkg" $(TARGET) || exit 1; \
		else \
			echo "==> $$pkg: sin Makefile todavía, se omite"; \
		fi; \
	done
