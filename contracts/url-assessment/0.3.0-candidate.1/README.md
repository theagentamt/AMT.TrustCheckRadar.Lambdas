# URL assessment candidate.3

Version `0.3.0-candidate.1` adds explicit Google Lookup freshness without modifying `v1-draft`. The original `assessedAt` is settlement time, not preparation time. Google `observedAt` is captured at the actual Lookup response (whole-second representation), and must not exceed the original assessment time. `validUntil` preserves Google's original strict UTC timestamp, including fractional precision; it is unrelated to receipt or operation expiration.

Evidence freshness is current, expired, unverified or observation_only. Current/expired match retains nonempty recognized threat types and observedAt < validUntil. Unverified legacy match/unavailable retains null clocks and no threats. No-match requires an observation time, null validity and empty threat types. The first matched hop ends Lookup traversal, so the result cannot combine threats with mismatched deadlines.

The mapper validates chronological relationships in addition to the JSON schema. Presentation never turns expiry into safety or a refund. Receipt replay stores nothing new. All old contract files remain unchanged. Cache policy/storage and provider-live qualification remain separate unresolved SEC233 work.
