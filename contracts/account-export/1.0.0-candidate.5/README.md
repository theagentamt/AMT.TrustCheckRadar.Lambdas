# Export candidate.5: optional demographic-research profile

Supersedes candidate.4 only when the default-disabled demographic-research export gate is enabled with Play verification. Adds the current optional age-band/state profile, its unexpired operation receipt, and value-free consent audit. It does not add campaign enrichment, commercial use, raw submissions, provider data, or a new collection path. Prior candidates remain immutable.

Expired profile/operation rows remain excluded by the reader TTL rules. Consent audit is value-free and remains exportable for its approved 400-day lifetime. Deployment and client qualification are separate release gates.
