PKG      := remonter_le_temps
VERSION  := $(shell sed -n 's/^version=//p' $(PKG)/metadata.txt)
QGIS_DIR ?= $(HOME)/.local/share/QGIS/QGIS3/profiles/default/python/plugins

.PHONY: help zip clean install uninstall lint

help:
	@echo "make zip        construit dist/$(PKG)-$(VERSION).zip"
	@echo "make install    copie l'extension dans le profil QGIS local"
	@echo "make uninstall  retire l'extension du profil QGIS local"
	@echo "make lint       verification syntaxique des modules"
	@echo "make clean      supprime dist/ et les caches"

zip:
	python3 scripts/build_zip.py

install:
	mkdir -p $(QGIS_DIR)
	rm -rf $(QGIS_DIR)/$(PKG)
	cp -r $(PKG) $(QGIS_DIR)/$(PKG)
	@echo "Installe dans $(QGIS_DIR)/$(PKG) - relancez QGIS."

uninstall:
	rm -rf $(QGIS_DIR)/$(PKG)

lint:
	python3 -m compileall -q $(PKG)

clean:
	rm -rf dist build
	find . -name '__pycache__' -type d -exec rm -rf {} +
