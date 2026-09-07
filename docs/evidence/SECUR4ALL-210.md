# SECUR4ALL-210 Lambda Evidence

Status: **Local validation complete; final UAT gate blocked**

The reproducible local evidence target runs the complete test suite, compiles all
sources, validates shell scripts, builds every ZIP deterministically, inspects the
seven campaign ZIPs, statically checks campaign and analysis log templates, proves
that server feature-extraction code/build/publishing support is absent, and runs a
10,000-comparison local scoring benchmark:

```bash
make campaign-evidence
```

Covered automated evidence includes:

- explicit-consent negative paths and retry-stable random event identity;
- server-owned participation, quota cycling protection, one-read operation
  idempotency, withdrawal-race checks, and deletion-completion receipts;
- cross-environment, unknown-version, extra-field, and malformed-input rejection;
- email/phone leakage rejection and content-free log-template checks;
- opaque queue envelopes and partial batch failures;
- missing/deleted/suppressed/expired/replayed event no-ops;
- one-vector and three-submission contributor abuse caps;
- category conflicts, similarity weights, threshold behavior, and optimistic
  concurrency;
- tombstone-before-delete, active-period token scope, recomputation, threshold
  suppression, and period-key retirement decisions;
- unauthorized review, invalid transitions, privacy threshold, immutable audit,
  and emergency suppression;
- published-only trends access, count bands, localization, filtering, and
  pagination isolation;
- deterministic Python ZIP structure without cache files.

The local scoring benchmark is diagnostic evidence only. It is not represented as
Lambda p95 or cost evidence.

Latest local run (2026-09-07):

- 177 tests passed with 73 parameterized negative subtests.
- 62 campaign and analysis log templates passed the static content-free check.
- All seven campaign ZIP artifacts passed root-handler/cache inspection.
- The server feature-extractor absence check passed.
- 10,000 local 384-dimensional scoring comparisons completed in 194.195 ms
  (51,494.7 comparisons/second on this host). This is not a Lambda/UAT latency
  measurement.

Final completion requires evidence that cannot be generated correctly from this
repository/host:

1. Android-owned multilingual extractor quality, provenance, and performance evidence.
2. UAT re-identification/model-inversion and coordinated-poisoning review.
3. Deployed IAM negative tests and cross-environment AWS tests.
4. Deadline-bound explicit expiry/deletion after the missing expiration access
   pattern is supplied.
5. UAT p95 end-to-end latency, concurrency, failure injection, monthly cost, alarm,
   rollback, and go/no-go records.

The story must remain open until those results exist; unit tests or estimated cost
cannot substitute for them.
