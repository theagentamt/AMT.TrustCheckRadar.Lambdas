PYTHON ?= python3
PYTHON_VERSION ?= 3.13
LAMBDA_ARCH ?= arm64
DIST_DIR ?= dist
ARTIFACT_BUCKET ?=
AWS_REGION ?= us-east-1
RELEASE ?=
INCLUDE_OPTIONAL ?= true

.PHONY: test compile lint check campaign-evidence package package-no-deps publish clean

test:
	$(PYTHON) -m pytest -q

compile:
	$(PYTHON) -m compileall -q src tests

lint:
	shellcheck scripts/*.sh

check: test compile lint

campaign-evidence: check package-no-deps
	$(PYTHON) scripts/validate_campaign_lambdas.py --dist-dir "$(DIST_DIR)"

package:
	bash scripts/build_lambda_zip.sh --all --output-dir "$(DIST_DIR)" --python-version "$(PYTHON_VERSION)" --arch "$(LAMBDA_ARCH)"

package-no-deps:
	bash scripts/build_lambda_zip.sh --all --output-dir "$(DIST_DIR)" --python-version "$(PYTHON_VERSION)" --arch "$(LAMBDA_ARCH)" --skip-dependencies

publish: package
	@test -n "$(ARTIFACT_BUCKET)" || (echo "ARTIFACT_BUCKET is required" >&2; exit 2)
	@test -n "$(RELEASE)" || (echo "RELEASE is required" >&2; exit 2)
	bash scripts/upload_lambda_zips.sh --dist-dir "$(DIST_DIR)" --bucket "$(ARTIFACT_BUCKET)" --release "$(RELEASE)" --region "$(AWS_REGION)" --include-optional "$(INCLUDE_OPTIONAL)"

clean:
	rm -rf .build dist
