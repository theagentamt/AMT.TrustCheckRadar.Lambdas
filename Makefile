STACK_NAME ?= amt-security-for-all-lambdas
AWS_REGION ?= us-east-1
LAMBDA_SRC ?= src/incognito_write
ZIP_OUTPUT ?= function.zip
PYTHON_VERSION ?= 3.12
LAMBDA_ARCH ?= arm64
ARTIFACT_BUCKET ?= asecurityforall-dev-artifacts
ARTIFACT_KEY ?= identity/post-confirmation/v1.0.0/function.zip

.PHONY: build deploy local-invoke local-invoke-age logs zip upload-zip publish-zip

build:
	sam build --cached

deploy: build
	sam deploy --stack-name $(STACK_NAME) --region $(AWS_REGION) --capabilities CAPABILITY_IAM --resolve-s3 --confirm-changeset

local-invoke: build
	sam local invoke IncognitoWriteFunction --event events/incognito-write.json

local-invoke-age: build
	sam local invoke AgeAttestationFunction --event events/age-attestation.json

logs:
	sam logs --name IncognitoWriteFunction --stack-name $(STACK_NAME) --region $(AWS_REGION) --tail

zip:
	bash scripts/build_lambda_zip.sh --lambda-src $(LAMBDA_SRC) --output $(ZIP_OUTPUT) --python-version $(PYTHON_VERSION) --arch $(LAMBDA_ARCH)

upload-zip:
	bash scripts/upload_lambda_zip.sh --file $(ZIP_OUTPUT) --bucket $(ARTIFACT_BUCKET) --key $(ARTIFACT_KEY) --region $(AWS_REGION)

publish-zip: zip upload-zip
