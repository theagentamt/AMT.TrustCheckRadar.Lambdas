# Feedback candidate.2

The existing POST /v1/result-feedback accepts explicit
feedbackTransportVersion 1.0.0-feedback-candidate.2. Request/response fields and
private opinion policy are unchanged. The new version accepts guarded URL
assessmentTransportVersion 1.0.0-candidate.3 in addition to existing assessments.
Candidate.1 remains unchanged and rejects the new assessment reference.

The receipt must belong to the authenticated account and current active device;
proof, client ID, receipt ID, assessment version, content-free summary and original
expiry are checked. The write and duplicate acknowledgment keep existing atomic
device/deletion/receipt fences. Feedback performs no entitlement/provider lookup,
no accounting change and no evidence/retention mutation. An opinion is never a
new provider verdict. The guarded mapper is packaged without consumer runtime
or secret authority. Deployment remains separate.
