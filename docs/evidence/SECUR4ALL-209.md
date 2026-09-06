# SECUR4ALL-209 Lambda Evidence

Status: **Lambda portion complete (`SECUR4ALL-211`)**

Verified acceptance evidence:

- JWT authentication is required.
- DynamoDB access is fixed to `GSI1PK=STATE#PUBLISHED` on the publication index.
- Items without approved contributor and submission count bands are suppressed.
- Exact counts, centroids, tokens, event IDs, and source content are absent from
  the response.
- English and Spanish stable-taxonomy labels are returned; unknown future IDs are
  rendered safely.
- Category, risk, language, and ISO-week filters are validated.
- Page size is bounded by configuration.
- Pagination tokens expire, are environment-bound, and are rejected if their key
  attempts to target a non-published partition.
- Logs contain only operation, schema version, result, and result count.
- `campaign_trends.zip` packages with `app.py` at its root.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_trends
./scripts/build_lambda_zip.sh --function campaign_trends --skip-dependencies
```

This completes only the Lambda-owned API component. API Gateway deployment,
least-privilege infrastructure (`SECUR4ALL-212`), dashboards, and client rendering
are explicitly outside this Lambda repository and are not claimed here.
