# ATCR-187 early rejection diagnostics

The retained private evidence-role trial observed HTTP 200 in 4,125 ms, but no usage,
assessment or finite parser rejection stage. Its original unknown-stage outcome,
consumed fence and full budget hold remain unchanged. These new diagnostics cannot
recover or retroactively classify that response.

The instrumentation gap is reproducible offline. `proposer.propose` rejects
encoding, framing, declared/actual body bounds and read/deadline failures before
calling the response parser. The external recorder initialized its stage to null
and only filled it inside assessment parsing; raw JSON/key-safety guards also
preceded usage/assessment capture. Its broad exception handler then collapsed
those different failures to the same generic result. An in-memory HTTP 200/gzip
response reproduces that exact missing-stage shape using the retained code. This
establishes the recorder defect, not the original live call's rejection predicate.

The optional `on_transport_rejection` callback reports one closed stage name,
without payloads, header values, sizes, offsets, prompts, credentials or exception
messages. Ordinary observer exceptions are ignored; request construction, guards,
error classes, timing, cleanup order, operations accounting and acceptance remain
unchanged. No observer is installed by production callers, and no logging is added.
Preparation, network/HTTP, response guards, callback and cleanup phases are covered.

A separately reviewed external recorder candidate, kept outside Lambda packaging,
adds `transportRejectionStage` and `recorderRejectionStage` to its result. It retains
the first known transport rejection if cleanup also fails; raw/decoded response
safety, JSON, usage and assessment boundaries get closed recorder stages. The
terminal summary independently allowlists all three diagnostic stage fields.
Only `one` and `summarize` change in that external recorder. Its one-call protocol,
key safeguards, source/scope binding, budget, timing, exclusive fence and no-retry
controls do not change. It is not new execution authority; old consumed approvals
and fences cannot authorize it.

No header/body acceptance rule, response-byte bound, prompt, private schema,
category definition, public response, production 512/eight-second setting,
qualification registry or frozen semantic rubric changes. Current unqualified
source-profile hashes are refreshed because the transport source hash changes;
historical identities remain immutable. Both original span-defect Astra attempts
remain exhausted. This is the first distinct Sol instrumentation-fix attempt.
Initial test-fixture failures and the failed live call remain retained separately.

The next live experiment remains held pending independent review and exact new
execution authority. No provider request, credential read, runtime activation,
infrastructure change or remote publication is performed by this patch.
