PYTHON ?= .cache/venv/bin/python

.PHONY: setup chain-up deploy dev build check test-contract test-integration test-security run-data run-travel collect-results reset-demo package
setup:
	python3 manage.py setup
chain-up:
	$(PYTHON) manage.py chain-up
deploy:
	$(PYTHON) manage.py deploy
dev:
	$(PYTHON) manage.py dev
build:
	$(PYTHON) manage.py build
check:
	$(PYTHON) -m compileall -q app.py manage.py services validators scripts tests
	pnpm --dir apps/web run typecheck
	$(PYTHON) -m pytest -q
test-contract:
	$(PYTHON) manage.py test-contract
test-integration:
	$(PYTHON) -m pytest -q
	$(PYTHON) tests/e2e/run_platform_acceptance.py
test-security:
	$(PYTHON) manage.py test-security
run-data:
	$(PYTHON) manage.py run-data
run-travel:
	$(PYTHON) manage.py run-travel
collect-results:
	$(PYTHON) manage.py collect-results
reset-demo:
	$(PYTHON) manage.py reset-demo --config .cache/local-project.json
package:
	$(PYTHON) manage.py package
besu-up:
	$(PYTHON) scripts/module_c.py besu-init
	docker compose -f infra/docker-compose.yml up -d
besu-down:
	docker compose -f infra/docker-compose.yml stop
