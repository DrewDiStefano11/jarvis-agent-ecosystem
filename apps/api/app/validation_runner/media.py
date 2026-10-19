"""Single-file, nonbootable ISO-9660 source media; no guest sharing service.

Fixed metadata prevents candidate filenames, symlinks, boot records or filesystem
extensions from entering the optical filesystem parser. The protected receiver
already knows the expected capsule; nothing on this medium establishes authority.
"""

import os
import re
import stat
import sys
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path
from struct import pack, unpack_from
from time import monotonic

from app.models.validation_cleanup import StorageRegistration
from app.tool_execution.filesystem import open_directory, read_bytes
from app.validation_runner.cleanup import deletion_handle, refuse
from app.validation_runner.isolation import verify_capsule
from app.validation_runner.source_transfer import MAX_PAYLOAD, receive_payload

SECTOR = 2048
DATA_SECTOR = 21
MAX_MEDIUM = DATA_SECTOR * SECTOR + MAX_PAYLOAD + SECTOR


def verify_media_observation(result, binding, state, image_digest, image_byte_count):
    expected = {
        "vmId": str(binding.vm_id),
        "hostMachineGuid": str(binding.host_machine_guid),
        "ownershipDigest": binding.ownership_digest,
        "mediaDigest": image_digest,
        "mediaBytes": image_byte_count,
        "mediaState": state,
        "commandOutcome": "unmeasured",
        "executionAuthorized": False,
    }
    if (
        type(result) is not dict
        or result.keys() != expected.keys()
        or any(
            type(result[key]) is not type(value) or result[key] != value
            for key, value in expected.items()
        )
    ):
        refuse()
    return result


def dual16(value):
    return pack("<H", value) + pack(">H", value)


def dual32(value):
    return pack("<I", value) + pack(">I", value)


def directory_record(name, location, size, *, directory=False):
    length = 33 + len(name) + (len(name) % 2 == 0)
    record = bytearray(length)
    record[0] = length
    record[2:10] = dual32(location)
    record[10:18] = dual32(size)
    record[18:25] = bytes((126, 1, 1, 0, 0, 0, 0))
    record[25] = 2 if directory else 0
    record[28:32] = dual16(1)
    record[32] = len(name)
    record[33 : 33 + len(name)] = name
    return bytes(record)


def media_prefix(payload_size):
    if type(payload_size) is not int or not 1 <= payload_size <= MAX_PAYLOAD:
        raise ValueError("SOURCE_MEDIA_SIZE_INVALID")
    prefix = bytearray(DATA_SECTOR * SECTOR)
    root_record = directory_record(b"\0", 20, SECTOR, directory=True)
    descriptor = bytearray(SECTOR)
    descriptor[:7] = b"\x01CD001\x01"
    descriptor[8:40] = b"JARVIS".ljust(32, b" ")
    descriptor[40:72] = b"JARVIS_SOURCE".ljust(32, b" ")
    descriptor[80:88] = dual32(DATA_SECTOR + (payload_size + SECTOR - 1) // SECTOR)
    descriptor[120:124] = dual16(1)
    descriptor[124:128] = dual16(1)
    descriptor[128:132] = dual16(SECTOR)
    descriptor[132:140] = dual32(10)
    descriptor[140:144] = pack("<I", 18)
    descriptor[148:152] = pack(">I", 19)
    descriptor[156:190] = root_record
    descriptor[190:813] = b" " * 623
    for start in (813, 830, 847, 864):
        descriptor[start : start + 17] = b"0" * 16 + b"\0"
    descriptor[881] = 1
    prefix[16 * SECTOR : 17 * SECTOR] = descriptor
    prefix[17 * SECTOR : 17 * SECTOR + 7] = b"\xffCD001\x01"
    prefix[18 * SECTOR : 18 * SECTOR + 10] = b"\x01\0" + pack("<I", 20) + pack("<H", 1) + b"\0\0"
    prefix[19 * SECTOR : 19 * SECTOR + 10] = b"\x01\0" + pack(">I", 20) + pack(">H", 1) + b"\0\0"
    records = root_record + directory_record(b"\x01", 20, SECTOR, directory=True)
    records += directory_record(b"CAPSULE.BIN;1", DATA_SECTOR, payload_size)
    prefix[20 * SECTOR : 20 * SECTOR + len(records)] = records
    return bytes(prefix)


def build_source_media(capsule, payload, *, authority_check):
    receive_payload(capsule, payload, authority_check=authority_check)
    medium = media_prefix(len(payload)) + payload + b"\0" * (-len(payload) % SECTOR)
    authority_check()
    return medium


def receive_source_media(capsule, medium, *, authority_check):
    authority_check()
    if type(medium) is not bytes or not DATA_SECTOR * SECTOR < len(medium) <= MAX_MEDIUM:
        raise ValueError("SOURCE_MEDIA_INVALID")
    # File record follows the two fixed 34-byte root directory records. Its
    # length/extents and every other metadata byte must equal our fixed format.
    length = unpack_from("<I", medium, 20 * SECTOR + 68 + 10)[0]
    prefix = media_prefix(length)
    end = DATA_SECTOR * SECTOR + length
    if (
        medium[: len(prefix)] != prefix
        or len(medium) != end + (-length % SECTOR)
        or any(medium[end:])
    ):
        raise ValueError("SOURCE_MEDIA_STRUCTURE_INVALID")
    payload = medium[DATA_SECTOR * SECTOR : end]
    receive_payload(capsule, payload, authority_check=authority_check)
    authority_check()
    return payload


@contextmanager
def pinned_source_media(
    root, registration, capsule, plan, *, image_digest, image_byte_count, authority_check
):
    """Hold enrolled source.iso and all ancestors immutable during native attach.

    The trusted guard authenticates original creation IDs, registered root, live
    plan/lease/approval and effect intent. It must remain in force until the guest
    is stopped and media detached; this context alone covers attachment only.
    After a crash the host must reopen/reverify the same registered image before
    continuing, never reconstruct ownership from candidate filesystem contents.
    """
    if os.name != "nt":
        refuse()
    registration = StorageRegistration.model_validate(registration.model_dump(mode="json"))
    capsule = verify_capsule(
        capsule,
        plan,
        owner=registration.owner,
        journal_digest=registration.journal_digest,
        native_inventory_digest=capsule.inventory_digest,
    )
    if registration.binding.capsule_digest != capsule.capsule_digest:
        refuse()
    root = Path(root)
    if not root.is_absolute() or str(root).startswith("\\\\"):
        refuse()
    if type(image_byte_count) is not int or not 1 <= image_byte_count <= MAX_MEDIUM:
        refuse()
    entry = next((item for item in registration.entries if item.path == "source.iso"), None)
    if entry is None or entry.kind != "file":
        refuse()

    def guard():
        authority_check(root, registration, capsule, plan)

    guard()
    with ExitStack() as stack:
        for path in (*reversed(root.parents), root):
            open_directory(stack, path, internal=True)
        stack.enter_context(
            deletion_handle(
                root,
                registration.dedicated_root_identity,
                directory=True,
                service_sid=registration.service_sid,
                deletable=False,
            )
        )
        allocation = root / registration.allocation_name
        stack.enter_context(
            deletion_handle(
                allocation,
                registration.allocation_identity,
                directory=True,
                service_sid=registration.service_sid,
                deletable=False,
            )
        )
        path = allocation / "source.iso"
        opened = stack.enter_context(
            deletion_handle(
                path,
                entry.identity,
                directory=False,
                service_sid=registration.service_sid,
                deletable=False,
            )
        )
        medium = read_bytes(opened.fd, image_byte_count)
        if len(medium) != image_byte_count or sha256(medium).hexdigest() != image_digest:
            refuse()
        receive_source_media(capsule, medium, authority_check=guard)
        guard()
        yield path
        guard()


def import_optical_source(
    device,
    root,
    capsule,
    *,
    image_digest,
    image_byte_count,
    device_number,
    authority_check,
    wall_seconds=120,
):
    """Read a registered read-only Linux optical block device, then import exactly.

    Expected capsule/hash/device identity come from the enrolled protected service
    control channel, not from optical content, candidate output or a guest ack.
    The host must still enforce the overall VM deadline independently. Blocking
    kernel device I/O cannot be safely bounded by a Python timestamp alone.
    """
    if sys.platform != "linux" or os.geteuid() != 0:
        refuse()
    if (
        type(device) is not str
        or not re.fullmatch(r"/dev/sr[0-9]{1,2}", device)
        or type(image_byte_count) is not int
        or not 1 <= image_byte_count <= MAX_MEDIUM
        or type(device_number) is not int
        or device_number < 0
        or type(wall_seconds) is not int
        or not 1 <= wall_seconds <= 300
    ):
        refuse()
    import fcntl

    deadline = monotonic() + wall_seconds

    def guard():
        if monotonic() > deadline:
            refuse()
        authority_check()

    guard()
    with ExitStack() as stack:
        dev = open_directory(stack, Path("/dev"), internal=True)
        info = os.fstat(dev.fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            refuse()
        fd = os.open(
            device.rsplit("/", 1)[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dev.fd
        )
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != device_number:
            refuse()
        readonly = bytearray(4)
        fcntl.ioctl(fd, 0x125E, readonly, True)  # BLKROGET, from linux/fs.h.
        if unpack_from("=I", readonly)[0] != 1:
            refuse()
        chunks, count = [], 0
        while count <= image_byte_count:
            guard()
            chunk = os.read(fd, min(65536, image_byte_count + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            chunks.append(chunk)
        medium = b"".join(chunks)
        if count != image_byte_count or sha256(medium).hexdigest() != image_digest:
            refuse()
        payload = receive_source_media(capsule, medium, authority_check=guard)
        from app.validation_runner.source_transfer import import_linux_guest

        return import_linux_guest(root, capsule, payload, authority_check=guard)


class NativeMediaDelivery:
    """Concrete stopped-VM attach/detach; acknowledgments never qualify execution.

    durable_effect owns persisted intent/checkpoint/claim/reconciliation. A restart
    must independently inspect the native state; it must not repeat an uncertain
    effect merely because an intent exists. No instance is registered by the app.
    """

    def __init__(self, adapter, *, root, authority_check, durable_effect):
        self.adapter = adapter
        self.root = Path(root)
        self.guard = authority_check
        self.effects = durable_effect

    def attach(self, registration, capsule, plan, *, image_digest, image_byte_count):
        binding = registration.binding
        intent = self.effects.prepare(
            registration, capsule, plan, "attach_media", image_digest, image_byte_count
        )
        self.effects.require_acknowledged(intent)
        # Claim is persisted before effects; it cannot be claimed again after a
        # crash. Recovery observes the selected drive instead of attaching again.
        self.effects.claim(intent)
        try:
            with pinned_source_media(
                self.root,
                registration,
                capsule,
                plan,
                image_digest=image_digest,
                image_byte_count=image_byte_count,
                authority_check=self.guard,
            ) as path:
                current = self.adapter.inspect(binding)
                if current.state != "off":
                    refuse()
                result = self.adapter.transport.invoke(
                    binding, "attach_media", media=(path, image_digest, image_byte_count)
                )
                self._verify(result, binding, "attached", image_digest, image_byte_count)
                self.effects.complete(intent, result)
                return result
        except BaseException:
            self.effects.uncertain(intent)
            raise

    def _verify(self, result, binding, state, image_digest, image_byte_count):
        verify_media_observation(result, binding, state, image_digest, image_byte_count)

    def detach(self, registration, capsule, plan, *, image_digest, image_byte_count):
        intent = self.effects.prepare(
            registration, capsule, plan, "detach_media", image_digest, image_byte_count
        )
        self.effects.require_acknowledged(intent)
        self.effects.claim(intent)
        try:
            self.guard(self.root, registration, capsule, plan)
            binding = registration.binding
            if self.adapter.inspect(binding).state != "off":
                refuse()
            path = self.root / registration.allocation_name / "source.iso"
            result = self.adapter.transport.invoke(
                binding, "detach_media", media=(path, image_digest, image_byte_count)
            )
            self._verify(result, binding, "detached", image_digest, image_byte_count)
            self.guard(self.root, registration, capsule, plan)
            self.effects.complete(intent, result)
            return result
        except BaseException:
            self.effects.uncertain(intent)
            raise

    def reconcile(self, intent, capsule, plan):
        """Inspect original media after restart; never repeat attach or detach."""
        from app.models.native_delivery import NativeDeliveryIntent

        intent = NativeDeliveryIntent.model_validate(intent.model_dump(mode="json"))
        if (
            intent.execution_plan_hash != plan.execution_plan_hash
            or intent.capsule_digest != capsule.capsule_digest
        ):
            refuse()
        self.effects.require_reconcilable(intent)
        registration = intent.registration
        with pinned_source_media(
            self.root,
            registration,
            capsule,
            plan,
            image_digest=intent.image_digest,
            image_byte_count=intent.image_byte_count,
            authority_check=self.guard,
        ) as path:
            result = self.adapter.transport.invoke(
                registration.binding,
                "inspect_media",
                media=(path, intent.image_digest, intent.image_byte_count),
            )
            self._verify(
                result,
                registration.binding,
                "attached" if intent.operation == "attach_media" else "detached",
                intent.image_digest,
                intent.image_byte_count,
            )
            self.guard(self.root, registration, capsule, plan)
            self.effects.reconcile(intent, result)
            return result
