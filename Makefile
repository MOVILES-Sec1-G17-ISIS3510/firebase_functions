SHELL := /bin/sh

FIREBASE ?= firebase
PROJECT ?= uniandessport
CODEBASE ?= community-notifs
FUNCTIONS_DIR ?= functions

.PHONY: help deploy deploy-functions deploy-all install emulators

help:
	@echo "Targets:"
	@echo "  make deploy          Deploy Firebase Functions for the default codebase"
	@echo "  make deploy-functions Same as deploy"
	@echo "  make deploy-all      Deploy all Firebase resources"
	@echo "  make install         Install Python dependencies for functions"
	@echo "  make emulators       Start Firebase emulators"

deploy:
	$(FIREBASE) deploy --only functions:$(CODEBASE) --project $(PROJECT)

deploy-functions: deploy

deploy-all:
	$(FIREBASE) deploy --project $(PROJECT)

install:
	cd $(FUNCTIONS_DIR) && python -m pip install -r requirements.txt

emulators:
	$(FIREBASE) emulators:start --only functions --project $(PROJECT)
