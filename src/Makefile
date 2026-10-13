PYTHON ?= .venv/bin/python

.PHONY: setup chain-up deploy build check test-contract test-integration test-security run-data collect-results besu-up besu-down
setup:
	bash scripts/setup.sh
chain-up:
	$(PYTHON) scripts/module_c.py chain-up
deploy:
	$(PYTHON) scripts/module_c.py deploy
build:
	$(PYTHON) scripts/module_c.py build
check:
	$(PYTHON) -m ruff check services scripts experiments tests infra
	$(PYTHON) -m pytest tests/integration/test_signing.py tests/integration/test_validation.py -q
test-contract:
	$(PYTHON) scripts/module_c.py test-contract
test-integration:
	$(PYTHON) -m pytest -q --junitxml=experiments/raw/python/pytest-results.xml
test-security:
	$(PYTHON) scripts/module_c.py test-security
run-data:
	$(PYTHON) scripts/module_c.py run-data
collect-results:
	$(PYTHON) scripts/module_c.py collect-results
besu-up:
	$(PYTHON) scripts/module_c.py besu-init
	docker compose -f infra/docker-compose.yml up -d
besu-down:
	docker compose -f infra/docker-compose.yml stop
