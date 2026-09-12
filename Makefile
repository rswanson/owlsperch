# Thin wrappers around the documented commands (see README.md, CLAUDE.md).
# Prefer running the underlying `uv run`/`npm` commands directly when you
# want more control (a single test, extra flags, etc.) -- these targets are
# just the "one command to remember" shortcuts.

.PHONY: dev test lint

dev:
	uv run owlsperch dev

test:
	uv run pytest pipeline/tests server/tests
	cd web && npm test -- --run

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy pipeline server
	cd web && npm run lint && npm run typecheck
