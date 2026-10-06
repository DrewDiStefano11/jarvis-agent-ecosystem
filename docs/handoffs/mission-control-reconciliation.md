# Mission Control reconciliation evidence

Latest fetched main: 05d7628b15b7665927584a7349ac3c1af9cf247c. Backend CI sharding PR #82 was confirmed merged at 2026-10-06T16:32:21Z before reconciliation. Each branch contains that main; conflicts were absent. Other sessions' branches were untouched. No PR was merged automatically.

| PR | Pushed exact head | Authoritative pull-request CI run |
| --- | --- | --- |
| 75 | 91e749c3e988fd9ac752750bf28022ab1dcefd28 | 37502646072 |
| 78 | f40acec96745ce9a50f97ff4c7c5f345ccde2c15 | 37502648785 |
| 80 | 02122a26cfaaf8bd6b1674ec5fc9c9c4c8c82bca | 37502651617 |
| 81 | 2d3d68782b510b59abf0bee2f7b1f854018f6c1b | 37502655870 |
| 83 | 08ef73e8c6402afad9e8f7915da749e0272af3b1 | 37502658093 |
| 84 | af250d2e044d07b1a886f67f9fcc586b2d3bea24 | 37502662880 |
| 85 | 36c3b4fea780927df279298cfa9ab7c1053a163d | 37502673191 |
| 86 | 5972eaba993ac74fb3bcefe4a37455d6fff51110 | 37502665034 |

Each exact head received an explicitly authorized independent local Codex review; results and limitations are recorded in its GitHub PR comments. System stale-resume/reset reconciliation, Approvals lost-ack synchronization and Activity bounded quoted-secret redaction findings were fixed and re-reviewed. Frontend test totals respectively: 116, 108, 111, 115, 115, 121, 113, 112; typecheck, ESLint and builds passed on every branch.

New sharded run 37498110186 diagnosed a runtime test crossing TestClient/pytest event loops and a models shard exceeding its process budget. Common three-file repair uses the public emergency-stop route in the correction regression and redistributes admission/verification tests across existing shards. No test skips or timeout increases. Ruff, 45 CI/correction tests, 1667-test collection/coverage and blank database migration cycle passed locally. Hosted fresh passes remain required; runtime failure was not reproduced locally and duration estimates are not guarantees.

Authoritative jobs must be backend-static, backend-migrations, backend-tests-runtime, backend-tests-autonomy, backend-tests-models and backend-tests-system on the listed exact heads. Old monolithic backend pytest runs and canceled duplicate push runs are not authoritative. At the previous natural checkpoint these fresh PR runs were queued; do not claim merge-ready before current checks pass. Recheck at meaningful work checkpoints, never idle polling CI. The operator requested stopping when only CI waits remain.
