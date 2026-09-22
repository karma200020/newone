PYTHON ?= python

.PHONY: test check evaluate run worker
test:
	$(PYTHON) -m pytest -q tests infra
	node --test tests/frontend.test.mjs

check:
	$(PYTHON) -m ruff check src tests scripts infra
	$(PYTHON) -m ruff format --check src tests scripts infra
	$(PYTHON) -m mypy src scripts infra

evaluate:
	$(PYTHON) -m ops.evaluation --baseline evaluation/baseline.json

run:
	$(PYTHON) -m ops.cli serve

worker:
	$(PYTHON) -m ops.cli worker
