# Modern campaign cleanup resource binding

The accepted SEC236 synthetic-account deletion exposed a campaign worker integration failure: `CleanupGuard` refused `describe_table`, which modern period-work proof and tracked transactions require to verify their pinned resources. The original deletion remains accepted; this change does not resubmit it or manufacture receipts.

The production fix adds only `describe_table` to the existing bounded client whitelist. It retains the 6,000 ms check before forwarding, receipt/job/control transaction guards, action limit, and rejection of other SDK methods. There are no permission, marker, schema, deadline, configuration, or public-contract changes. Only `campaign_deletion_bridge` needs an artifact update. Its independently qualified period-work inventory/source stays pinned to the existing production `9147d545` boundary; installation needs the separately reviewed narrow cleanup compatibility reference.

Validation from the repository root:

```sh
AMT_AUTHORITY_INTEGRATION=1 AWS_ACCESS_KEY_ID=testing AWS_SECRET_ACCESS_KEY=testing PYTHONPATH=src /tmp/amt-live-deletion-venv/bin/python -m pytest tests/campaign_deletion_bridge/test_modern_worker_binding_dynamodb.py tests/campaign_deletion_bridge/test_worker_completion_dynamodb.py tests/shared_campaign_work/test_completion_compatibility_dynamodb.py -q
```

All 37 tests passed. The five new tests produced three failures before the fix and all passed afterward. They exercise actual scheduled recovery through an OPEN period, tracked tombstone/work creation and CAMPAIGN receipt; actual stream handling through qualified SEALED/RETIRED proof without MAC; resource identity mismatch; budget exhaustion at the guarded call; and refusal of unapproved SDK methods. Independent review also ran the five new tests successfully.

These are actual boto3/Moto storage and handler composition tests with explicitly synthetic inventory, resource TableId/ARN metadata, clocks and KMS. They do not establish a deployed-runtime result or completion of the still-pending real synthetic-account operation. Production deployment and subsequent observation are separate root-owned steps; no account write, AWS invocation, key mutation or original-operation retry was performed for this source fix.
