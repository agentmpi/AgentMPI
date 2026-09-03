PY := .venv/bin/python
.PHONY: test lint bench paper macros bib check suite clean

test:
	$(PY) -m pytest conformance tests -q

lint:
	$(PY) -m ruff check ampi ampitools conformance tests experiments paper/tools

suite:
	$(PY) experiments/tools/collect_suite.py

bench:
	$(PY) experiments/e0_micro/run.py --reps 25 --scale 2,4,8,16

macros: suite
	$(PY) paper/tools/make_macros.py

bib:
	$(PY) paper/tools/build_bib.py

paper: macros bib
	cd paper && pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  bibtex main >/dev/null 2>&1 || true; \
	  cd paper && pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  pdflatex -interaction=nonstopmode main.tex >/dev/null && \
	  pdfinfo main.pdf | grep -i pages

check: lint test
	$(PY) paper/tools/check_tex.py

clean:
	rm -f paper/*.aux paper/*.log paper/*.out paper/*.bbl paper/*.blg
