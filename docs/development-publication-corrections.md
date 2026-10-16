# Publication-source correction bridge (in progress)

The publication adapter and contract participate in the admission implementation
digest. Durable proposal replay additionally binds the validated protected
publication anchor when present; changing its reference or native evidence cannot
reuse the original command. Non-publication replay hashes remain unchanged.
The adapter contract tests are assigned to the models CI shard, with complete
full-suite collection checked before publication.

Campaign A accepts an optional publication operation/head/observation reference during correction workspace preview and approval. It resolves that reference only through `app.state.development_publication_source_reader`. The reader is absent by default; requests requiring it fail with `DEVELOPMENT_PUBLICATION_LINK_UNAVAILABLE`. No HTTP payload can supply the resolved anchor or install a reader.

The future Campaign C adapter must read its protected source link under the authenticated execution owner's durable owner/RBAC scope and return a `PublicationSourceAnchor`. The preview, approval and historical replay paths all supply that execution owner. The separately authenticated operator approves the exact destination plan; historical reads cannot borrow or fabricate operator authority. A reader authorization refusal propagates without fallback. A compares task, original workspace, source-edit identity, mutation checkpoint, parent code-state hash, and exact published head. Anchors are historical references with mutation and validation authority fixed false. They require a complete correction lineage and enter the exact approved admission hash. Existing admissions omit absent anchors to preserve their serialized hash shape.

An advanced-head destination must have the anchored commit as both base and HEAD, no working-tree changes, and the captured bytes and modes of every selected baseline source. Unread selected files cannot satisfy this comparison. Native workspace ownership, independent exact-plan approvals, task budgets, source measurement, and recovery guards remain required.

This adapter interface is not yet wired to Campaign C's protected implementation. Boundary tests use deterministic fixtures and prove only contract/refusal behavior. Real Git publication-to-correction execution, restart recovery across that chain, and Campaign B's isolated validation still require integration evidence. Live inference remains disabled pending independently verified subscription-only usage and recharge safeguards; no API key or separate API inference is activated.

The committed-head case in `tests/test_development_lineage.py` exercises two actual native source effects in separate owned workspaces and a real isolated Git commit between them. It verifies that the second physical plan changes only `source.py` against its committed `value = 2` identity; `new.py` is already present and preserved. The first checkout remains unchanged by the correction. Independent exact-hash approval, shared dispatch budget, acknowledgement-gap recovery, missing/changed anchor refusal, emergency stop, and application restart are checked. Successful restart replay does not recall the provider.

The commit is produced by the test harness in its temporary registered repository. Its publication anchor and model responses are deterministic fixtures. Thus the passing native case establishes A's committed-head compilation, mutation, and recovery behavior, but does not establish Campaign C's authenticated publication/source-link integration or Campaign B's genuine isolated validation. Campaign C's source/PR base separation remains a reported, open dependency in draft #151; no code from that branch is imported here.
