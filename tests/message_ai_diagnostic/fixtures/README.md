This nonpersonal request is the exact historical PR166 dev-019-en 512-token/low
request used by the frozen one-call diagnostic. Its complete byte SHA256 is
`9048e86d653ceded89669654867f6737a0a2fb8eac1513500db751bd97d1dd36`;
its system-instruction SHA256 is
`b85d182c0a2a81327b0629a7f7fdbeedc36e5b963bdb496f90c735f2751035f6`.
It contains no credentials or authentication headers.

These unit/functional tests temporarily select that historical system instruction
to exercise the real recorder parser/guard paths against their original request
binding. The fixture does not authorize execution, repin the recorder, change
production instructions or reset any consumed fence. A separate test restores
the current prompt and verifies the unchanged request-drift guard stops before
secret, network or parser work. Current-prompt projection and qualification gates
are covered in tests/message_evaluator/test_per_reason_evidence.py.
