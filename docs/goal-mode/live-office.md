# Persisted live office

The Office page places real runtime identities on the original Floor 1 artwork. Select an identity, choose a verified desk and an original sprite, then select **Assign identity**. **Inspect destination** and **Focus identity** provide useful camera views. Select a connected destination and **Move identity** to issue a durable movement intent. The canvas follows the persisted route; it never schedules decorative movement or pretends an identity is doing work.

**Stop movement** freezes the current position. Refreshing or restarting the API reconstructs the same stopped position. **Continue move** resumes the remaining original path. **Release desk assignment** removes the placement, retaining its command/version history. An identity must be active and enabled to assign or move. Emergency stop freezes movement and blocks new movement; resuming the system still requires an explicit continuation. Assignment and movement do not grant any execution, task, or identity permissions.

The badges project coarse actual runtime activity: queued, working, waiting, failed, completed, or idle. The original sprites have cardinal walking and idle/offline clips. They use the honest idle fallback while the activity badge reports work; they do not fabricate typing, conversations, or meetings. The Planning link retains the existing task-scoped authorization for details and results.

## Spatial scope and evidence

The live network includes eight stations and eight directed routes. The six existing desks remain: Models A ↔ B (`POSITION_022` / `POSITION_028`), Models review A ↔ B (`POSITION_030` / `POSITION_032`), and Focus C A ↔ B (`POSITION_120` / `POSITION_121`). Security desk (`POSITION_020`) now connects in both directions to the west walkway (`WALKWAY_D42`) through general-access automatic door D42. Each direction follows 51 checked points. Other cross-room connections remain unavailable, and the full candidate overlay remains explicitly unapproved.

`office-crossing-d42.json` records the finite correction: the exact authored floor-path vertex `(3133.395556, 1705.285333)` replaces D42's elevated panel centroid as its foot-level threshold. A single 11.720687px join connects the two interrupted Security pen strokes. The approach, doorway and exit were inspected over the original floor image. No global stroke snapping, collider removal, aperture expansion, room remapping or approval-flag change is applied. The unchanged 96px doorway aperture and 34px agent footprint must fit in both directions. D42's existing general-access policy authorizes automatic opening for an active identity; its background artwork remains the original static floor image.

The API accepts station IDs only; clients cannot supply coordinates, paths, door overrides, or approval flags. It reserves both departure and destination stations during movement. A single movement reserves the office aisle conservatively, while multiple identities can occupy separate desks and execute real tasks concurrently. Stationary identity footprints are checked against each requested route. An occupied or unavailable route returns a specific error, preserving the current placement.

The original sources are pinned to prototype commit `7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd`:

- `scripts/vendor/office-navigation.ts` preserves `src/office/floor1/navigation/candidateNavigation.ts` byte for byte (LF SHA-256 `abff5ee7af915d83eb246363ad23c3cce80ae402251e4b8bcd8c1f6eedbc660f`). The generator verifies this checksum, then applies the bounded `office-door-connectors.cjs` correction: authorized door IDs now reach endpoint connectors as well as walk-network edges. All original collision, topology, access and point-limit checks remain in use.
- Sprite types/resolver originate from `src/office/sprites/` at the same commit. The resolver adds only safe static-frame fallbacks for strict indexed access. The manifest retains the two original approved cardinal sheets, clip definitions, anchors and fallback graph. `scripts/office-assets.json` pins all artwork checksums; `pnpm --dir apps/web office:assets` installs/verifies the ignored PNG files without committing generated binaries.
- `docs/goal-mode/office-registration.json` records 15 independently matched physical features across all four quadrants, covering 92.3% of embedded image width and 88.6% of height. The original 6144×4096 JPEG was read directly from the pinned Rooms.pdf stream, avoiding a re-encoded image. The existing transform `scale=4/3, offset=(0,-2/3)` has maximum residual **1.183183 production pixels**, RMS **0.617821 pixels**. Crop pairs were visually inspected; the selected paths were also overlaid on the production floor and inspected for desk alignment and clearance.

Measured registration verifies image alignment, not all authored geometry. The source wrappers retain `productionApproved: false`. The generator constructs a private review context with measured landmarks solely to evaluate this finite subset; it does not promote or mutate the candidate wrappers. The stored candidate coordinates are already transformed and are not transformed a second time. No production-wide approval artifact is fabricated.

Run `node scripts/build-office-network.cjs` after frontend dependency installation to reproduce eight valid routes, four representative blocked/restricted route probes, 260 colliders, and 47 door policies. It also rejects D42 transit under blocked, restricted, reserved, manual-review and malformed policies, an unapproved wall crossing, the missing stroke join and the old panel center. It rejects unexpected geometry or vendor changes. Use `--write` only when deliberately regenerating and reviewing the catalog. Numeric registration can independently be reproduced with optional Pillow/numpy/pypdf installed:

```powershell
python scripts/verify-office-registration.py --pdf C:\path\to\pinned\Rooms.pdf --output $env:TEMP\office-registration
```

The script checks PDF/JPEG/floor checksums, remeasures every landmark, and writes diagnostic crop pairs to the requested directory. It changes no approval state. The PDF is not a runtime dependency.

## Contracts and recovery

`GET /api/office` returns the OpenAPI `OfficeSnapshot`, including the immutable catalog, current placements, server time, emergency stop and persistent `placementVersions`. Commands use `POST /api/office/identities/{identity_id}/commands` with a unique command ID and expected placement version. A command replay with the same content returns the original result; reusing an ID for different work is rejected. Version checks also cover release and reassignment. An uncertain browser acknowledgement offers explicit retry of the same command; synchronization never silently repeats a POST.

Migration `20260905_07` stores placement/motion and command receipts. Services/repositories own transactions, and routes contain no SQL. Placement changes, append-only audit records and globally ordered outbox events commit together. The shared event cursor is refreshed without replacing unrelated cached workflow state. The existing AppStore event/resync path refreshes one canonical office projection; the canvas derives temporary animation positions from that projection and server time. It creates no separate domain store or socket.

Active moves recover from persisted start time, duration and validated polyline. A move whose duration elapsed while the API was down settles at its reserved destination after restart. Stopped moves remain stopped. Lifecycle and emergency-stop transitions persist a frozen position so a later activation cannot resume it implicitly. The dedicated reconciliation loop settles arrivals without generating random commands or runtime executions.

## Validation

Backend tests cover isolated database migration, concurrency, idempotent commands, version conflicts, reservation/route restrictions, stopped and active recovery, inactive/emergency behavior, truthful activity and outbox/audit consistency. Frontend tests cover server-clock interpolation, frozen stops, stale refresh rejection, explicit command replay and authoritative conflict recovery. The original candidate contract tests preserve unapproved source flags.

The local browser acceptance uses an isolated database and dynamic loopback ports:

```powershell
$env:JARVIS_SMOKE_BROWSER='true'
$env:JARVIS_SMOKE_OFFICE='true'
$env:SMOKE_ARTIFACT_DIR="$env:TEMP\jarvis-live-office-qa"
python scripts/smoke-local-planning.py
```

It exercises two real identities with the original sprites, assignment, move/stop/continue, refresh reconstruction, an actual separate worker execution using an explicitly labelled deterministic HTTP fixture, emergency stop, release/reassignment, and mobile layout. The fixture is transport/recovery evidence, not evidence of real model reasoning. CI runs the navigation verifier and the browser acceptance independently of the existing planning golden path.
