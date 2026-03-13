SPHINXBUILD   = sphinx-build
SOURCEDIR     = source
BUILDDIR      = _build

.PHONY: html clean serve

html:
	$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html -W

serve: html
	@echo "Serving docs at http://localhost:8000"
	python3 -m http.server 8000 --directory $(BUILDDIR)/html

clean:
	rm -rf $(BUILDDIR)
