PYTHON ?= python3

.PHONY: check governance test build
check: governance test
	$(PYTHON) scripts/check_provenance.py
	git diff --check

governance:
	$(PYTHON) scripts/check_repository.py

test:
	$(PYTHON) tests/test_review_conductor.py
	$(PYTHON) tests/test_review_conductor_activation.py
	$(PYTHON) tests/test_review_conductor_userland.py
	$(PYTHON) tests/test_review_conductor_profiles.py
	$(PYTHON) -m unittest discover -s tests -p 'test_scaffold.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_trusted_admission.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_service_runtime.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_*guard.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_launcher_transport.py' -v
	$(PYTHON) -m unittest discover -s tests -p 'test_workflow_contract.py' -v

build:
	$(PYTHON) scripts/build.py
