# SECUR4ALL-210 Lambda Evidence

Status: **Local validation complete; final UAT gate blocked**

The reproducible local evidence target runs the complete test suite, compiles all
sources, validates shell scripts, builds every ZIP deterministically, inspects the
six campaign ZIPs, statically checks campaign log templates, proves the feature
model is revision-pinned/offline-only, and runs a 10,000-comparison local scoring
benchmark:

```bash
make campaign-evidence
```

Covered automated evidence includes:

- explicit-consent negative paths and retry-stable random event identity;
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

Latest local run (2026-09-06):

- 144 tests passed with 22 parameterized negative subtests.
- 10 campaign log templates passed the static content-free check.
- All six ZIP artifacts passed root-handler/cache inspection.
- The pinned offline model revision check passed for
  `b8ef00830037f9868450f778081ea683e900fe39`.
- 10,000 local 384-dimensional scoring comparisons completed in 193.270 ms
  (51,741.2 comparisons/second on this host). This is not a Lambda/UAT latency
  measurement.

Final completion requires evidence that cannot be generated correctly from this
repository/host:

1. Approved English/Spanish fixtures and the immutable feature image digest for
   measured 95% precision and 80% recall.
2. UAT re-identification/model-inversion and coordinated-poisoning review.
3. Deployed IAM negative tests and cross-environment AWS tests.
4. Deadline-bound explicit expiry/deletion after the missing expiration access
   pattern is supplied.
5. UAT p95 end-to-end latency, concurrency, failure injection, monthly cost, alarm,
   rollback, and go/no-go records.

The story must remain open until those results exist; unit tests or estimated cost
cannot substitute for them.
