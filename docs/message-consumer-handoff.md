# Governed message backend integration

## Current implementation and provenance

The separate message consumer/evaluator implement prepare, submit and proof-only
reconcile against the existing V1 authority. No legacy FREE/PRO/research balance,
conversation history or second ledger is authoritative. The coherent previously
approved deletion/inventory/expiry prerequisite 2f277a1 was normally merged into
this branch (merge c35ffdaf334b1a861c8fda5ef34e9e7c03b8eb55), preserving all-key
deletion, inventory fences, OPEN/CLOSED preparation admission and seven-day cleanup.
Existing URL schema bytes and URL payload HMAC purpose are unchanged by the message
increment. Message intent uses a distinct account-bound HMAC purpose and route-kind
reconciliation fencing; no raw text, original URL or OCR enters authority storage.

Published contract candidate is contracts/message-consumer/1.0.0-candidate.1,
initial source pin be4890d5929906c43bc80ecb5941b99b5038c0de. Policy approval and exact
historical source fingerprint are recorded in sec229-message-policy-approval.md.
Runtime tests validate actual evaluator/envelope outputs and transactional authority
behavior. Synthetic fixtures do not establish live provider or billing evidence.

## Runtime and infrastructure

Python 3.14 ARM64 handlers are message_consumer.app.lambda_handler and
message_evaluator.app.lambda_handler. Consumer timeout 29 seconds, evaluator 23;
consumer passes at most 18000ms, preserving settlement headroom and checking actual
context time. Private evaluation accepts at most one explicitly reviewed link.
SDK retries are disabled. Private response bodies are bounded to 16384 bytes and
reject duplicate JSON keys. There is no model client, prompt execution, provider
free-text rendering or AI secret in this bounded increment.

Consumer requires existing shared authority configuration, AUTHORITY_ENABLED=true,
STAGE=dev, an explicit engineering subject allowlist and MESSAGE_CONSUMER_ENABLED=true.
Evaluator separately requires MESSAGE_EVALUATOR_ENABLED=true. Both require exact
MESSAGE_POLICY_VERSION=message-rules-2026-09-20-v1 and MESSAGE_POLICY_APPROVAL_SHA256
from the approval record. All activation flags remain false in infrastructure.

MESSAGE_EVALUATOR_FUNCTION_ARN is the exact Dev message-evaluator:live alias.
Evaluator URL_ASSESSMENT_FUNCTION_ARN is the exact Dev url-assessment:live alias.
Consumer reads exact AWSCURRENT HMAC and identity/authority rows; writes use existing
transaction guards. No Query, DeleteItem, history storage or provider secret access
is needed by consumer. Evaluator requires only private URL Lambda invocation and
has no storage/secret permissions. Packages keep evaluator independent of consumer
and authority imports. Existing lifecycle workers own expiry/account deletion.

Mandatory positive engineering settings MESSAGE_PROVIDER_WINDOW_SECONDS,
MESSAGE_PROVIDER_ATTEMPTS_PER_WINDOW and MESSAGE_PROVIDER_FAILURES_PER_WINDOW have
no runtime defaults. MESSAGE_PROVIDER_CIRCUIT_OPEN defaults true. The aggregate
V1#CONTROL / MESSAGE_PROVIDER_BUDGET metadata row tracks a CAS revision, window,
attempts and failures, never accounts/content. Every evaluator dispatch consumes an
attempt regardless of outcome, paid/trial/complimentary basis or completed-check
allowance. Window/failure caps stop further calls; a configured open circuit stops
all. No SDK or server automatic resubmission occurs. Account attempt-window rate
limits supply explicit retryAfterSeconds; budget-limited settled outcomes release
the check without charge, and a later new check remains subject to the same hard
provider window cap. Exact operating values require scoped release qualification.

## Honest scope and release acceptance

The approved bounded verifier currently qualifies ten normalized whole-message
EN/ES cases. It is not general NLP: arbitrary paraphrases, quotation, uncertain
speaker roles, and unsupported context yield actual inconclusive results. Only
complete qualified results charge one. Blocked/inconclusive/partial/unavailable
settle zero; uncertain transport retains unknown accounting until reconciliation.
A provider no-match does not clear message content, and an observed-chain threat
match is retained as partial high risk without inventing its exact redirect hop.

Android initially transmits only exact reviewed sanitized text/tokens, explicit
whole-message speaker role and withheld-link coverage. It sends no reviewed link
values. A later disclosure UI is separate. Built-in approved recovery guidance
remains independent of remote assessment/accounting. No arbitrary model action or
URL is accepted. Server residual-identifier checks are bounded and do not promise
complete PII removal or anonymity.

Before activation, qualify actual device/account/deletion gates, runtime settings,
provider-window limits/deadlines, payload rejection before private calls, lost
responses/replay, incomplete zero settlement, complete single charge, and proof-only
recovery on scoped synthetic Dev identity. Paid renewal/catalog/verified ownership
lifecycle remains a separate fail-closed dependency; this code does not mint paid
periods from client claims. No AWS deployment, live assessment evidence or full
ATCR-120 closure follows merely from contract publication or passing local tests.

## Source validation evidence

Python 3.14 normal suite: 764 passed, 166 subtests passed, 9 expected isolated
module skips. Separate Moto authority/message run: 273 passed, including 14
independently authored lifecycle/budget cases. Policy/privacy/precedence coverage
includes 33 evaluator tests. Three contract/handler/package tests validate checksums,
all full-envelope fixtures, disabled-service accounting and actual ZIP imports.
Both message artifacts built with full Python 3.14 ARM64 dependency wheels; source
compile and shellcheck pass. Existing URL contract directories have no diff against
the coherent approved prerequisite 2f277a1. This is local/emulator/package evidence,
not evidence of AWS activation or paid-store lifecycle verification.
