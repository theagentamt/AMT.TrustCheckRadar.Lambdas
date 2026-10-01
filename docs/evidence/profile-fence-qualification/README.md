# Profile-fence artifact publication

Reviewed Lambda source `c4b1cae34b9a90a9803022302a6ad7f2d8a13e38` was integrated
through PR50 into `release-V01` at `320417765d7fea89454dc9cf4272b7edb048acf6`.
Only its two profile-writer archives were published to the versioned Dev artifact
bucket; no Lambda, IAM, account or provider mutation occurred during publication.

- [Local qualification](../../profile-fence-qualification.md): 30 real-SDK/Moto
  cases, 6 existing SDK-double cases, full Python3.14 ARM64 builds and five
  extracted-package synthetic transaction checks.
- [Local package checks](local-packages.json): each ZIP has exactly `app.py`
  matching its committed source, with no bundled native or third-party dependency.
- [Publication manifest](publication.json): immutable source-prefix object keys,
  exact S3 VersionIds, SHA256/base64 hashes, sizes, handlers and runtime targets.
- [Independent downloads](download-verification.json): each exact published
  version was downloaded separately and compared byte-for-byte with the reviewed
  local ZIP, then its source member was compared with the immutable Git commit.

The operator harness narrowed the existing reviewed immutable publisher to
`post_confirmation` and `age_attestation`. It checked clean exact source, release
ancestry, committed local evidence and archive source bytes before any AWS call;
conditional S3 writes cannot replace an existing object with different bytes.
This evidence does not claim running-function updates, live AWS SDK compatibility,
actual IAM authorization or signup/age behavior. Infrastructure owns the separate
coordinated active-handler installation and verification; deletion/export and
checkpoint policy gates remain unchanged.
