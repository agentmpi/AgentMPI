.PHONY: help install test lint paper tables clean microbench translation software campaign-status traces viz

PY ?= python3
AMPI ?= $(HOME)/.local/bin/ampi

help:
	@echo "install       install the package in editable mode"
	@echo "test          run the test suite"
	@echo "tables        regenerate the paper's tables from results/"
	@echo "paper         build paper/agentmpi.pdf"
	@echo "microbench    run the agent-free microbenchmarks"
	@echo "translation   run the translation experiment with the synthetic executor"
	@echo "software      run the software experiment with the synthetic executor"
	@echo "clean         remove build and LaTeX intermediates"

install:
	$(PY) -m pip install -e .

test:
	$(PY) -m pytest -q

# The microbenchmarks that need no agents: deterministic, free, and the ones that
# validate the implementation against its own cost model.
microbench:
	$(PY) experiments/microbench.py --bench collectives --bench faults \
		--bench transport --bench scaling --label free --root runs/mb-free

translation:
	$(PY) experiments/translation.py --ranks 4 --executor function \
		--root runs/tr-synth --label tr-synth

software:
	$(PY) experiments/software.py --ranks 4 --rounds 1 --executor function \
		--root runs/sw-synth --label sw-synth

tables:
	$(PY) scripts/make_tables.py

# Export run traces to static JSON under viz/public/traces, so the viewer works from a
# fresh clone with no server and no experiments run. The run directories themselves are
# hundreds of megabytes and gitignored; this is the derived view, ~600 KiB, committed.
traces:
	$(PY) scripts/export_traces.py --min-events 40 \
		--exclude smoke --exclude "__coll-" --exclude "__transport-" --exclude "__pingpong"

# The viewer. Reads the live trace server if one is running, otherwise the exported
# traces. Start the server separately for live runs:
#   python3 scripts/trace_server.py --runs runs
viz:
	cd viz && npm install --silent && npm run dev

# Three passes plus bibtex: the cross-references, the bibliography, and then the
# page balancing all need a settled .aux file.
#
# SOURCE_DATE_EPOCH and FORCE_SOURCE_DATE make the output byte-reproducible. Without
# them pdflatex embeds the build time, so the checked-in PDF shows a diff on every
# rebuild even when nothing changed -- which trains you to ignore diffs on it.
PAPER_EPOCH ?= 1700000000

paper: tables
	cd paper && SOURCE_DATE_EPOCH=$(PAPER_EPOCH) FORCE_SOURCE_DATE=1 pdflatex -interaction=nonstopmode agentmpi.tex >/dev/null
	cd paper && bibtex agentmpi >/dev/null || true
	cd paper && SOURCE_DATE_EPOCH=$(PAPER_EPOCH) FORCE_SOURCE_DATE=1 pdflatex -interaction=nonstopmode agentmpi.tex >/dev/null
	cd paper && SOURCE_DATE_EPOCH=$(PAPER_EPOCH) FORCE_SOURCE_DATE=1 pdflatex -interaction=nonstopmode agentmpi.tex >/dev/null
	@echo "built paper/agentmpi.pdf"

campaign-status:
	$(PY) experiments/campaign.py --dir runs/campaign --status

clean:
	rm -rf build dist src/*.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	cd paper && rm -f *.aux *.log *.out *.bbl *.blg *.toc *.fls *.fdb_latexmk *.synctex.gz
