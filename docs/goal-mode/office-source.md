# Office integration provenance and visual contract

Canonical reference: `DrewDiStefano11/jarvis-office-prototype`, commit
`7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd`. The user referred to it as
`agent-office-prototype`; repository discovery found the canonical name above.
The reference checkout is untouched.

The Hub imports the original DOM/SVG viewport, camera coordinate system,
pan/pinch gestures, interaction renderer, layer ordering, access interpretation,
and candidate geometry validation into `apps/web/src/office-reference`. This
replaces the existing CSS room grid on the same `/office` page; it is not another
application, router, backend, or event stream. The Office reads shared AppStore
workforce and task state. Large rendering code is loaded only on that route.

The 8192×5460 original image is immutable, uniformly scaled, and rendered with
nearest-neighbor behavior. Its SHA-256 is
`aa0ff821d5530e8ee1be3c1de733d0bff4bb74767a0ba27e48a141abf784d026`.
`scripts/office-assets.json` pins the exact source; the installer verifies bytes.
It is the original floor shown in the user's references, not a regenerated image.

The nine copied candidate documents preserve 907 authored entities: 34 rooms,
167 walk paths, 82 walls, 178 objects, 47 doors, 144 door lights, 44 computers,
205 positions and six interactive objects. Wrapper approval guards remain active.
Red doors remain locked; seat yellow/red remains priority/standard. No numbering
from the phone screenshots is embedded into worker sprites.

Adaptations to upstream renderer code:

- Removed development-only random/ambient fixture-agent mounts and simulation
  panels from the live Hub viewport.
- Preserved pan, pinch, fit, focus and drag-versus-selection handling.
- Added explicit bounds checks required by the Hub's strict indexed-access typing.
- Corrected React ref initialization and sprite reset lifecycle without disabling
  lint rules; retained original gesture and coordinate regression tests.
- Reduced non-scaling SVG outlines to one pixel so candidate inspection does not
  obscure the floor at its normal fitted scale.
- Resolved candidate JSON through the Hub's public asset path rather than the
  prototype's development-only middleware endpoint.

## Registration and navigation boundary

Upstream `candidateRegistration.ts` still records zero measured landmarks,
`status: unverified`, and `productionApproved: false`. The source's strict
production loader therefore cannot authorize navigation. The Hub preserves that
boundary: candidate inspection is opt-in, live workers are explicitly unplaced,
and no animation is used to imply that a worker moved or executed a tool.

The original `candidateNavigation`, `continuousNavigation`, and `prototypeRuntime`
modules remain available in the reference repository. They have not been replaced
with scripted movement. Integrating their approved routes requires measured floor
registration, explicit identity-to-desk assignments, and backend movement intent.
These are still critical unfinished office capabilities. Sprite animation and the
Nexus tube reference are not yet bound to live worker state.

## Visual verification ledger

| Reference requirement | Implementation evidence | Runtime visual status |
| --- | --- | --- |
| Exact floor artwork and room arrangement | Original pinned bytes; no image editing | Verified in CI desktop screenshot; original source itself reaches canvas edges |
| Consistent scale and camera | Original source dimensions, uniform transform, coordinate/gesture tests | CI camera/desktop/mobile smoke passed on a964d02 |
| Room/door/path selection | Original candidate entities and interaction renderer | CI region selection/focus smoke passed on a964d02 |
| Live workforce truth | Shared identity/runtime/task state; unplaced identities and labeled simulation | UI contract tests pass |
| Sprites, walking, door transitions | No invented placement or movement | Unfinished pending registration/bindings |

The cloud browser rejected localhost with `ERR_BLOCKED_BY_CLIENT`. Local Chromium
was absent and its download timed out. Do not interpret source reuse, tests, or a
successful build as completed screenshot comparison. The new CI browser job saves
actual desktop/mobile office images and planning completion evidence when it runs.

The CI screenshots from `a964d028d3cbc507577e22d2afcae312e692401e` were retrieved
and visually inspected. The original floor, central hub, departments and camera
framing render correctly. This inspection found default browser control styling
and a stale identity operational-status label beside healthy runtime telemetry.
A follow-up corrects control/link contrast, labels identity enablement separately
from worker health, reduces mobile dead space and resets scroll for mobile shots.
The original source itself reaches the canvas edges; the viewport adds no crop.
