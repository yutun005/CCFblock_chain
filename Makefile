.PHONY: setup build check test-integration test-contract test-security test-e2e test-travel run-data run-travel collect-results reset-demo package dev

setup:
	python manage.py setup

build:
	python manage.py build

check:
	python -m py_compile app.py manage.py
	python -m unittest discover -s tests -v

test-integration:
	python -m unittest discover -s tests -v

test-contract:
	python manage.py test-contract

test-security:
	python manage.py test-security

test-e2e:
	python manage.py test-e2e

test-travel:
	python manage.py run-travel

run-data:
	python manage.py run-data

run-travel:
	python manage.py run-travel

collect-results:
	python manage.py collect-results

reset-demo:
	python manage.py reset-demo

package:
	python manage.py package

dev:
	python manage.py start
