PYTHON ?= python

.PHONY: bootstrap-index
bootstrap-index:  ## Create + schema-bootstrap every index DuckDB (empty, idempotent)
	$(PYTHON) -m open_pulse_sources.index._federated.bootstrap
