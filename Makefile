PY := .venv/bin/python
.PHONY: test lint bench paper macros bib check suite clean viz-install viz-build viz-api viz-dev

test:
	$(PY) -m pytest conformance tests -q

lint:
	$(PY) -m ruff check ampi conformance tests experiments scripts

suite:
	$(PY) scripts/collect_suite.py

bench:
	$(PY) experiments/e0_micro/run.py --reps 25 --scale 2,4,8,16

macros: suite
	$(PY) scripts/make_macros.py

bib:
	$(PY) scripts/build_bib.py

paper: macros bib
	(cd paper && pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  bibtex main >/dev/null 2>&1) || true; \
	(cd paper && pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  pdfinfo main.pdf | grep -i pages)

check: lint test
	$(PY) scripts/check_tex.py

clean:
	rm -f paper/*.aux paper/*.log paper/*.out paper/*.bbl paper/*.blg

viz-install:
	cd viz && npm ci

viz-build:
	cd viz && npm run build

viz-api:
	$(PY) scripts/trace_server.py --runs runs

viz-dev:
	cd viz && npm run dev
