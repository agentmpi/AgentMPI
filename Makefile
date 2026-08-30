PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
export PYTHONPATH := $(CURDIR)/src

.PHONY: help venv test lint microbench faults corpus traces figures paper viewer clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

venv: ## create the virtualenv and install the package
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev,tokens,plots]"

test: ## run the full test suite
	$(PY) -m pytest tests -q

microbench: ## collective scaling to 128 ranks
	$(PY) experiments/microbench.py --max-p 128 \
	  --out experiments/results/microbench.json

faults: ## fault injection: detect, revoke, shrink, recover
	$(PY) experiments/faults.py --sizes 8,16 --failures 1,2,4 --repeats 2 \
	  --out experiments/results/faults.json

corpus: ## build the translation corpus from Project Gutenberg
	curl -sS https://www.gutenberg.org/files/11/11-0.txt \
	  -o experiments/data/alice_raw.txt
	$(PY) experiments/prepare_corpus.py --chunks 12 --max-chars 13000

translation-setup: ## create a translation run and write one prompt per rank
	$(PY) experiments/translation/setup_run.py --name $(NAME) \
	  --workers $(or $(WORKERS),12) --mode $(or $(MODE),glossary)

software-setup: ## create a collaborative software run
	$(PY) experiments/software/setup_run.py --name $(or $(NAME),tinyq) \
	  --modules $(or $(MODULES),8)

traces: ## export traces for the viewer
	$(PY) -m agentmpi.cli trace export --root runs/real-glossary/ampi \
	  --out viewer/public/traces/translation.jsonl
	$(PY) -m agentmpi.cli trace export --root runs/tinyq/ampi \
	  --out viewer/public/traces/tinyq.jsonl

figures: ## regenerate the paper's figures from recorded run data
	$(PY) experiments/make_figures.py

tables: ## regenerate the paper's tables from recorded run data
	$(PY) experiments/summarize_translation.py
	$(PY) experiments/make_tables.py

paper: figures tables ## build paper/main.pdf
	cd paper && latexmk -pdf -interaction=nonstopmode main.tex

viewer: ## run the trace viewer at http://127.0.0.1:43917
	cd viewer && npm install && npm run dev

clean:
	rm -rf paper/*.aux paper/*.bbl paper/*.blg paper/*.log paper/*.out \
	       paper/*.fls paper/*.fdb_latexmk .pytest_cache
