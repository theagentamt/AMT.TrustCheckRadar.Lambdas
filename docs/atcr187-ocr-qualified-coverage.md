# Single-screenshot qualified rule coverage

The retained real Dev M6 check reviewed this text with the other-person speaker
selected, then returned an inconclusive, uncharged rules-only result:

```text
Screenshot 1:
Pay this tax fee by buying gift cards and
send me the numbers and PINs.
```

The payment/action phrase already belongs to `DEMAND_GIFT_CARD_PAYMENT`.
Case folding and whitespace normalization already handle `PINs` and the OCR line
break. Android's `mergeScreenshotText` adds the literal `Screenshot 1:` label;
the backend's whole-message lookup consequently rejects the labelled message.
This is reproduced offline from the retained reviewed text. The mobile evidence
does not contain the captured HTTP body or a check ID, so source/UI correlation is
not represented as an independent wire capture.

Coverage version `qualified-phrases-2026-10-29-v2` adds 136 exact labelled risky
forms (68 per language), derived from the existing closed phrase families. They
apply only when the existing source field is `ocr`. The matcher compares the
entire normalized message; it does not strip arbitrary headers, extract sentences
or broaden gift-card word matching. The declared source is not trusted evidence
of meaning or speaker identity. Quotation, negation, extra context, multiple
screenshots, mixed/pasted sources and uncertain/self speakers remain outside these
new forms. No new benign OCR forms are qualified. Existing unlabelled coverage
and withheld-link limitations remain in place.

Neither reviewed text nor provider input is rewritten. Public response schemas,
copy, authentication, authorization, strict validation, AI prompts/configuration,
budgets and complete-only accounting are unchanged. A complete supported OCR
warning uses the existing qualified-rules basis and existing one-check settlement;
incomplete results remain uncharged. The current unqualified evaluation source
identities/checksums are refreshed for the changed rule source, preserving every
historical identity and all qualification/paid-execution flags.

Offline regressions cover all finite risky forms, paired contextual and speaker
boundaries, benign gift-card mentions, missing action elements, OCR line/case/tab
normalization, uncorrected recognition errors and existing privacy rejection.
Isolated unit/functional authority tests cover complete-only settlement, replay,
no raw-text retention and no provider budget/call. These tests do not establish a
new live result. After independent review/source integration, a separately scoped
Dev evaluator rollout and one real OCR check are still needed. AI qualification
remains NO-GO; production/public activation remains blocked.
