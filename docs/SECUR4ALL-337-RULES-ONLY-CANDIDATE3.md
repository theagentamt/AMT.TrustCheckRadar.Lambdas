# SECUR4ALL-337 candidate.3 rules-only source boundary

This source increment separates the approved candidate.3 transport from the
future AI qualification. It does not activate a Lambda, provider, customer, or
subject.

The consumer and evaluator must both receive the trusted runtime setting
`MESSAGE_CANDIDATE3_RULES_ONLY_ENABLED=true`. The setting is accepted only in
Dev with the existing evaluator, authority, engineering-subject, policy-version,
and policy-approval gates. Rules-only and `MESSAGE_AI_ENABLED=true` are mutually
exclusive. Candidate.2 still requires its AI gate and cannot fall back to this
mode.

For candidate.3 rules-only submissions:

- the consumer does not reserve the external-provider budget;
- the evaluator does not call Google Web Risk or AI, including when the
  sanitized request contains reviewed links;
- only the existing exact English and Spanish local rule coverage can complete;
- unsupported text is `inconclusive`, uses `INSUFFICIENT_EVIDENCE`, reports
  `aiAssessmentStatus=not_assessed`, and charges zero checks;
- hostile-input stops stay `blocked` and charge zero checks;
- reviewed links are unassessed rather than described as a provider outage;
- settlement, reconciliation, account ownership, active-device enforcement,
  allowance accounting, and candidate.3 freshness presentation remain on the
  existing authority path.

Infrastructure must keep the new setting false and every subject allowlist
empty until the separate scoped Dev qualification is approved. The evaluator
role needs no provider secret and no URL-assessment invoke permission for this
mode.

## Reproducible source checks

Run with Python 3.14 and the pinned authority test dependencies:

```text
python -m pytest -q \
  tests/message_evaluator/test_candidate3_rules_only.py \
  tests/message_evaluator/test_ai_policy.py \
  tests/message_evaluator/test_freshness.py \
  tests/message_consumer/test_contract_and_handler.py

AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q \
  tests/message_consumer/test_freshness_integration.py \
  tests/message_consumer/test_integration.py
```

These are source and isolated DynamoDB tests. They do not claim a deployed Dev,
Android, UAT, AI-model, Google-provider, or customer-access qualification.
