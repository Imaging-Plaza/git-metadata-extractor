PYTHON ?= python

.PHONY: bootstrap-index
bootstrap-index:  ## Create + schema-bootstrap every index DuckDB (empty, idempotent)
	$(PYTHON) -m src.index._federated.bootstrap
