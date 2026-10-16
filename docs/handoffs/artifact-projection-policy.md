# Artifact projection admission invalidation

Baseline capture derives expected artifacts through
`source_mutation/artifacts.py`, which calls
`source_edit_planning/projection.py`. The development admission policy digest
includes that projection implementation so an implementation change requires
fresh admission approval.

The existing dependency-upgrade regression simulates a projection source change,
checks that the policy digest changes, and verifies that the earlier persisted
admission is rejected with `DEVELOPMENT_ADMISSION_CONFLICT`.
