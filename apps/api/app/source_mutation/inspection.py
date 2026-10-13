"""Fenced read-only native inspection of a completed source mutation.

Never call mutation apply/prepare/acknowledge or the namespace creation helper.
Only existing directories/files are opened. Native source and metadata pins span
measurement and the final live authority/namespace checks.
"""

import json
import os
from contextlib import ExitStack
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.models.source_artifact_verification import (
    ArtifactMeasurement,
    NativeArtifactVerification,
)
from app.models.source_change import source_path
from app.models.source_mutation import ApplySourceMutation
from app.models.source_mutation_inspection import MutationInspection
from app.self_build.atomic_directory import pinned_target_directory
from app.self_build.checkout_driver import CheckoutDriver, pin_read_tree, supports_mutation
from app.self_build.creation_service import ownership
from app.self_build.materialization import blob_digest, inventory
from app.self_build.policy import digest
from app.source_mutation.artifacts import plan_expected_artifacts
from app.source_mutation.driver import (
    measure_code_state,
    observe,
    pin_source_tree,
    refuse,
    staging_names,
    verify_base_index,
    verify_creation_marker,
)
from app.source_mutation.history import MutationHistoryReader
from app.source_mutation.native import read_pin
from app.tool_execution.filesystem import open_directory, open_file, read_bytes, windows_native_path


class MutationInspector:
    def __init__(self, app):
        self.history = MutationHistoryReader(app)
        self.service = self.history.service

    def inspect(self, actor, request):
        return self._inspect(actor, request, artifacts=False)

    def verify_artifacts(self, actor, request):
        """Measure exact expected artifacts under the same live native source pins."""
        return self._inspect(actor, request, artifacts=True)

    def _inspect(self, actor, request, *, artifacts):
        try:
            request = ApplySourceMutation.model_validate(
                request.model_dump(mode="json", warnings=False)
            )
            history = self.history.read(actor, request.editing_record_id)
            record, proposal, creation, nonce = self.service.current(actor, request)
            if record != history.record:
                refuse("SOURCE_MUTATION_POSITION_CONFLICT")
            if record.state != "complete":
                refuse("SOURCE_MUTATION_INSPECTION_INCOMPLETE")
            if not history.native_checkpoint_present or not history.journal_checkpoint_acknowledged:
                refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")
            if not supports_mutation():
                refuse("SOURCE_MUTATION_PLATFORM_UNAVAILABLE")

            def authority_check():
                current, _, _, _ = self.service.current(actor, request)
                if current != record:
                    refuse("SOURCE_MUTATION_POSITION_CONFLICT")

            # The registry is keyed by configured repository ID, not source identity.
            with self.service.repository.sessions() as session:
                row = self.service.repository.lookup(session, record.plan.workspace_id)
                policy = self.service.workspace.policies.get(row.repository_id)
            checkout_driver = CheckoutDriver(self.service.creation.git.tool(), authority_check)

            def final_guard():
                authority_check()
                current_history = self.history.read(actor, request.editing_record_id)
                if current_history.record != record:
                    refuse("SOURCE_MUTATION_POSITION_CONFLICT")
                if (
                    not current_history.native_checkpoint_present
                    or not current_history.journal_checkpoint_acknowledged
                ):
                    refuse("SOURCE_MUTATION_CHECKPOINT_INVALID")

            return self._measure(
                checkout_driver,
                policy,
                creation,
                nonce,
                proposal,
                record,
                final_guard,
                artifacts=artifacts,
            )
        except SQLAlchemyError:
            refuse("SOURCE_MUTATION_STORAGE_FAILED")
        except OSError:
            refuse("SOURCE_MUTATION_NATIVE_CONFLICT")
        except (AttributeError, KeyError, TypeError, ValueError):
            refuse("SOURCE_MUTATION_INSPECTION_INVALID")

    @staticmethod
    def _read(directory, name, limit):
        with open_file(directory, name, os.O_RDONLY, internal=True) as fd:
            return read_bytes(fd, limit)

    @staticmethod
    def _require_absent(stack, path):
        # lexists/stat conveniences may silently swallow access or long-path
        # errors. Only native FILE_NOT_FOUND proves absence in a pinned parent.
        try:
            stack.enter_context(read_pin(path))
        except FileNotFoundError:
            return
        refuse("SOURCE_MUTATION_FOREIGN_ARTIFACT")

    @staticmethod
    def _check_namespace_names(target, ignored):
        nodes = 0

        def walk_error(error):
            raise error

        for parent, directories, files in os.walk(target, followlinks=False, onerror=walk_error):
            nodes += len(directories) + len(files)
            if nodes > 8192:
                refuse("SOURCE_MUTATION_INVENTORY_LIMIT")
            names = [*directories, *files]
            if len({name.casefold() for name in names}) != len(names):
                refuse("SOURCE_MUTATION_INSPECTION_PATH_COLLISION")
            for name in names:
                path = (Path(parent) / name).relative_to(target).as_posix()
                if path in {".git", ".jarvis-workspace.json"}:
                    continue
                if path in ignored and name in files:
                    continue
                if any(
                    part.casefold() == ".git" or part.casefold().startswith(".jarvis-")
                    for part in path.split("/")
                ):
                    refuse("SOURCE_MUTATION_FOREIGN_ARTIFACT")
                source_path(path)

    def _measure(
        self, driver, policy, creation, nonce, proposal, record, final_guard, *, artifacts=False
    ):
        tool = driver.observer
        if (
            tool.executable_hash != creation.plan.tool_sha256
            or digest([str(tool.executable), tool.executable_hash])
            != creation.plan.tool_identity_digest
        ):
            refuse("SOURCE_MUTATION_TOOL_CHANGED")
        root_path = Path(policy.worktree_root)
        key = creation.plan.workspace_id
        source_plan = proposal.plan.source_plan
        manifest = plan_expected_artifacts(record, proposal.plan) if artifacts else None
        measurements = []
        owner = ownership(creation, nonce)
        with ExitStack() as stack:
            root = open_directory(stack, Path(root_path.anchor), internal=True)
            for leaf in root_path.parts[1:]:
                root = open_directory(stack, root.path / leaf, root, leaf, internal=True)
            private = open_directory(
                stack,
                root.path / (".jarvis-owner-" + key),
                root,
                ".jarvis-owner-" + key,
                internal=True,
            )
            target, info = pinned_target_directory(stack, root, key, create=False)
            checkout = open_directory(stack, target, root, key, internal=True)
            stack.enter_context(read_pin(private.path / "owner.json"))
            encoded_owner = json.dumps(owner, sort_keys=True, separators=(",", ":")).encode()
            if self._read(private, "owner.json", 8192) != encoded_owner:
                refuse("SOURCE_MUTATION_INSPECTION_OWNERSHIP_CONFLICT")
            stack.enter_context(read_pin(private.path / "target.json"))
            expected_claim = dict(
                workspaceId=key,
                ownershipDigest=digest(owner),
                device=info.st_dev,
                inode=info.st_ino,
            )
            if json.loads(self._read(private, "target.json", 4096)) != expected_claim:
                refuse("SOURCE_MUTATION_INSPECTION_OWNERSHIP_CONFLICT")
            driver.authority_check()
            driver.validate_existing_target(stack, root, target, policy, creation.plan)
            check_metadata = pin_read_tree(
                stack,
                Path(policy.primary_root) / ".git",
                driver.authority_check,
                maximum=creation.plan.maximum_initial_metadata_nodes
                + creation.plan.metadata_growth_reserve,
            )
            # Pin every source and marker before any source measurement. No lock
            # file is opened/created, and no creation-capable namespace API is used.
            check_source = pin_source_tree(stack, target, driver.authority_check)
            verify_creation_marker(checkout, creation.plan)
            verify_base_index(driver, policy, creation.plan, target)
            base = {}
            for mode, blob, path, size in inventory(driver, policy, creation.plan, target):
                data = driver.invoke(policy, "blob", creation.plan, target, blob=blob)
                if len(data) != size or blob_digest(data) != blob:
                    refuse("SOURCE_MUTATION_BASE_CONFLICT")
                base[path] = (sha256(data).hexdigest(), mode)
            ignored = set()
            modes = {}
            for index, change in enumerate(source_plan.changes):
                mode = (change.before or change.after).mode
                modes[change.path] = mode
                parent = change.path.rpartition("/")[0]
                before_name, after_name = staging_names(record.operation_id, index)
                for name in (before_name, after_name):
                    ignored.add(parent + "/" + name if parent else name)
                before_path = target.joinpath(*change.path.split("/")[:-1], before_name)
                after_path = before_path.with_name(after_name)
                # Completed operations must retain exactly their approved original
                # bytes, with no stage left behind. Never adopt or remove artifacts.
                if change.before:
                    stack.enter_context(read_pin(before_path))
                    parent = open_directory(stack, before_path.parent, internal=True)
                    data = self._read(parent, before_name, creation.plan.maximum_file_bytes)
                    if (len(data), sha256(data).hexdigest()) != (
                        change.before.byte_count,
                        change.before.digest,
                    ):
                        refuse("SOURCE_MUTATION_BACKUP_CONFLICT")
                    if artifacts:
                        # read_pin already denies writes/deletion and verifies the
                        # canonical single-link file. Identity is measured, never
                        # promoted to durable provenance or a cleanup grant.
                        info = os.stat(windows_native_path(before_path), follow_symlinks=False)
                        measurements.append(
                            ArtifactMeasurement(
                                path=before_path.relative_to(target).as_posix(),
                                presence="retained_original",
                                digest=sha256(data).hexdigest(),
                                byte_count=len(data),
                                device=info.st_dev,
                                inode=info.st_ino,
                            )
                        )
                else:
                    self._require_absent(stack, before_path)
                    if artifacts:
                        measurements.append(
                            ArtifactMeasurement(
                                path=before_path.relative_to(target).as_posix(),
                                presence="absent",
                                digest=None,
                                byte_count=None,
                                device=None,
                                inode=None,
                            )
                        )
                self._require_absent(stack, after_path)
                if artifacts:
                    measurements.append(
                        ArtifactMeasurement(
                            path=after_path.relative_to(target).as_posix(),
                            presence="absent",
                            digest=None,
                            byte_count=None,
                            device=None,
                            inode=None,
                        )
                    )
            self._check_namespace_names(target, ignored)
            observation, _ = observe(stack, checkout, source_plan, modes, ignored)
            current_state, _ = measure_code_state(
                stack,
                checkout,
                source_plan.source,
                base,
                creation.plan,
                ignored,
                driver.authority_check,
            )
            observation_digest = digest(observation.model_dump(mode="json"))
            matches = (
                current_state == proposal.plan.expected_code_state
                and observation_digest == record.final_observation_digest
            )
            payload = dict(
                operation_id=record.operation_id,
                mutation_plan_hash=record.plan.plan_hash,
                checkpoint_id=record.checkpoint_id,
                code_state=current_state,
                source_observation_digest=observation_digest,
                matches_completed_mutation=matches,
                current_source_measured=True,
                execution_authorized=False,
            )
            final_guard()
            check_metadata()
            check_source()
            result = MutationInspection(
                **payload,
                inspection_fingerprint=digest(
                    payload | {"code_state": current_state.model_dump(mode="json")}
                ),
                measured_at=datetime.now(UTC),
            )
            if not artifacts:
                return result
            payload = dict(
                schema_version="1.0",
                manifest=manifest.model_dump(mode="json"),
                source_inspection=result.model_dump(mode="json", exclude={"measured_at"}),
                measurements=[item.model_dump(mode="json") for item in measurements],
                native_artifacts_verified=True,
                execution_authorized=False,
                deletion_authorized=False,
                durable_artifact_ownership_verified=False,
            )
            return NativeArtifactVerification(
                **(payload | {"source_inspection": result}),
                verification_fingerprint=digest(payload),
            )
