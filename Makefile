.PHONY: install skill cluster-up cluster-down chaos chat test test-integration rollback clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

skill:
	bash scripts/install_skill.sh

cluster-up:
	bash scripts/setup_cluster.sh

cluster-down:
	kind delete cluster --name k8s-troubleshoot

chaos:
	@test -n "$(SCENARIO)" || (echo "Usage: make chaos SCENARIO=crashloop|oom|bad_probe|missing_config" && exit 1)
	$(PYTHON) scripts/chaos.py --scenario $(SCENARIO)

chat:
	$(PYTHON) -m agent.cli

test:
	$(PYTHON) -m pytest -m "not integration"

test-integration:
	$(PYTHON) -m pytest -m integration

rollback:
	@test -n "$(RESOURCE)" || (echo "Usage: make rollback RESOURCE=deployment/web" && exit 1)
	RESOURCE="$(RESOURCE)" $(PYTHON) -c "import os; from agent import kube; k, n = os.environ['RESOURCE'].split('/'); print(kube.rollback(k, n))"

clean:
	rm -rf $(VENV) .pytest_cache
	find . -name '__pycache__' -type d -exec rm -rf {} +
	rm -f backups/*.yaml backups/audit.log
