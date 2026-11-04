"""Trusted reviewed-preview translation. The reviewed self app remains refused by admission."""

import asyncio
import socket
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from oversight.workspace.locks import offload
from oversight.workspace.preview_boundary import BoundaryHandle, ReadOnlyMount
from oversight.workspace.preview_boundary_entry import (
    SYSTEM_DIRECTORIES,
    SYSTEM_FILES,
    TOOL_TARGETS,
    manifest_digest,
)
from oversight.workspace.preview_gateway import PreviewGateway
from oversight.workspace.preview_launch_spec import PreviewLaunchSpec
from oversight.workspace.preview_preparation import (
    PreparationInputs,
    PreparedPreview,
    prepare_preview,
    verify_prepared_manifests,
)
from oversight.workspace.preview_profile import PreviewProfile
from oversight.workspace.preview_publication import PublishedBuild
from oversight.workspace.preview_registry import PreviewRegistry

REPO_ROOT = Path(__file__).resolve().parents[4]


def overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def canonical_exclusions(exclusions: tuple[Path, ...]) -> tuple[Path, ...]:
    # Canonical operands also cover hosts with aliases for HOME, /mnt or /run.
    return tuple(
        dict.fromkeys(
            p.resolve() for p in (*exclusions, REPO_ROOT, Path.home(), Path("/mnt"), Path("/run"))
        )
    )


def validate_storage(inputs: PreparationInputs) -> None:
    exclusions = canonical_exclusions(inputs.excluded_roots)
    writable = (inputs.tree, inputs.preview_directory, inputs.preparation_storage)
    if any(overlaps(path.resolve(), excluded) for path in writable for excluded in exclusions):
        raise ValueError("reviewed writable storage overlaps an excluded root")


async def prepare_reviewed(inputs: PreparationInputs) -> PreparedPreview:
    validate_storage(inputs)
    # The leaf's async API currently does synchronous inventory/copy work. Run its entire
    # coroutine on a worker loop, and settle that worker before releasing any ownership.
    return await offload(lambda: asyncio.run(prepare_preview(inputs)))


@dataclass(frozen=True)
class ReviewedRoots:
    tree: Path
    state: Path
    scratch: Path
    readonly: tuple[ReadOnlyMount, ...]
    exclusions: tuple[Path, ...]


def boundary_roots(prepared: PreparedPreview, exclusions: tuple[Path, ...]) -> ReviewedRoots:
    excluded = canonical_exclusions(exclusions)
    scratch = prepared.root
    writable = (prepared.tree, prepared.state, scratch)
    if any(overlaps(p.resolve(strict=True), e) for p in writable for e in excluded):
        raise ValueError("reviewed writable root overlaps an excluded root")
    verify_prepared_manifests(prepared)
    roles = {target: role for role, target in TOOL_TARGETS.items()}
    mounts: list[ReadOnlyMount] = []
    for mapping in prepared.root_mappings:
        if not mapping.read_only:
            if (mapping.source, mapping.target) not in (
                (prepared.tree, Path("/T")),
                (prepared.state, Path("/state")),
            ):
                raise ValueError("unexpected writable root mapping")
            continue
        source = mapping.source.resolve(strict=True)
        if any(overlaps(source, e) for e in (*excluded, *writable)):
            raise ValueError("reviewed mount overlaps an excluded root")
        if not any(i.root == source for i in prepared.manifests):
            raise ValueError("unrecorded boundary mount")
        target = mapping.target.as_posix()
        role = roles.get(target)
        if role is None and mapping.target.is_relative_to("/opt/deps"):
            role = "dependencies"
        if role is not None:
            mounts.append(ReadOnlyMount(source, target, role, manifest_digest(source)))
    # Preparation can inventory a directory of selected system entries. The boundary
    # mounts only its fixed individual system files/directories, using the real system
    # identity and comparing it with the recorded prepared bytes first.
    for target in (*SYSTEM_FILES, *SYSTEM_DIRECTORIES):
        matches = [
            m
            for m in prepared.root_mappings
            if m.read_only and Path(target).is_relative_to(m.target)
        ]
        if len(matches) != 1:
            raise ValueError("missing recorded system mount")
        mapping = matches[0]
        selected = mapping.source / Path(target).relative_to(mapping.target)
        source = Path(target).resolve(strict=True)
        digest = manifest_digest(source)
        if manifest_digest(selected.resolve(strict=True)) != digest:
            raise ValueError("recorded system mount changed")
        mounts.append(ReadOnlyMount(source, target, "system", digest))
    if {(m.source, m.target) for m in prepared.root_mappings if not m.read_only} != {
        (prepared.tree, Path("/T")),
        (prepared.state, Path("/state")),
    }:
        raise ValueError("missing writable root mapping")
    return ReviewedRoots(prepared.tree, prepared.state, scratch, tuple(mounts), excluded)


# This bootstrap runs only after authenticated entry, inside the allowlisted root.
# It executes exactly one frozen argv, without a shell or import of application code.
_EXEC = """
import json, os, resource, sys
value = json.loads(sys.argv[1])
os.environ.clear()
os.environ.update(value['env'])
os.environ['NODE_OPTIONS'] = '--max-old-space-size=' + str(value['node_heap_mib'])
os.nice(10)
cpus = sorted(os.sched_getaffinity(0))[:value['cores']]
os.sched_setaffinity(0, cpus)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (value['file_bytes'], value['file_bytes']))
if value['python']:
    limit = value['python_bytes']
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
os.chdir(value['cwd'])
os.execvpe(value['argv'][0], value['argv'], os.environ)
"""


def translate_path(path: Path, inputs: PreparationInputs) -> str:
    """Translate only frozen absolute paths with one explicit mount owner."""
    if path == inputs.preview_directory / "state" or path.is_relative_to(
        inputs.preview_directory / "state"
    ):
        return str(Path("/state") / path.relative_to(inputs.preview_directory / "state"))
    matches = [m for m in inputs.root_mappings if path.is_relative_to(m.source)]
    if len(matches) != 1:
        raise ValueError("frozen command path has no unique root mapping")
    mapping = matches[0]
    return str(mapping.target / path.relative_to(mapping.source))


def verify_tool_bindings(spec: PreviewLaunchSpec, inputs: PreparationInputs) -> None:
    for index, name in ((0, "python"), (1, "node")):
        tools = [tool for tool in inputs.tools if tool.name == name]
        if len(tools) != 1 or (
            translate_path(spec.resources[index], inputs) != str(tools[0].path)
            or spec.executables[index].sha256 != tools[0].sha256
        ):
            raise ValueError("prepared tool does not match frozen executable identity")


def reviewed_configuration(
    profile: PreviewProfile,
    inputs: PreparationInputs,
    worktree: Path,
    parent_authority: str,
    run_id: str,
) -> dict[str, Any]:
    """Verify process-local authority before serializing trusted supervisor-only data."""
    import json
    from dataclasses import asdict

    authority = profile.reviewed_app
    if authority is None or profile.trusted_demo:
        raise ValueError("reviewed authority required")
    profile.validate()
    authority.validate()
    spec = authority.launch
    if inputs.tree != worktree or spec.state != inputs.preview_directory / "state":
        raise ValueError("reviewed preparation does not match frozen paths")
    if spec.api_port == spec.public_port or {spec.api_port, spec.public_port} - set(profile.ports):
        raise ValueError("reviewed API and public ports must be distinct")
    validate_storage(inputs)
    verify_tool_bindings(spec, inputs)
    commands: list[dict[str, Any]] = []
    for command in (*spec.build, spec.start):
        argv = tuple(
            translate_path(Path(a), inputs) if a.startswith("/") else a for a in command.argv
        )
        commands.append(
            dict(argv=argv, cwd=translate_path(command.cwd, inputs), timeout_s=command.timeout_s)
        )
    return json.loads(
        json.dumps(
            dict(
                inputs=asdict(inputs),
                commands=commands,
                policy={
                    name: value
                    for name, value in vars(profile).items()
                    if type(value) in (int, float)
                },
                parent_authority=parent_authority,
                run_id=run_id,
                api_port=spec.api_port,
                public_port=spec.public_port,
            ),
            default=str,
        )
    )


def preparation_inputs(raw: dict[str, Any]) -> PreparationInputs:
    """Decode private registry data, never a model/client declaration."""
    from oversight.workspace.preview_preparation import (
        Inventory,
        InventoryEntry,
        ProjectImport,
        RootMapping,
        ToolIdentity,
    )

    values = dict(raw)
    for name in (
        "tree",
        "preview_directory",
        "preparation_storage",
        "project_environment",
        "demo_python",
    ):
        values[name] = Path(values[name])
    values["excluded_roots"] = tuple(map(Path, values["excluded_roots"]))
    values["system_entries"] = tuple(map(Path, values["system_entries"]))
    values["inventories"] = tuple(
        Inventory(
            i["name"],
            i["role"],
            Path(i["root"]),
            tuple(InventoryEntry(**e) for e in i["entries"]),
            i["digest"],
            i["owned"],
        )
        for i in values["inventories"]
    )
    values["tools"] = tuple(
        ToolIdentity(t["name"], Path(t["path"]), t["sha256"]) for t in values["tools"]
    )
    values["root_mappings"] = tuple(
        RootMapping(Path(m["source"]), Path(m["target"]), m["read_only"])
        for m in values["root_mappings"]
    )
    values["imports"] = tuple(
        ProjectImport(
            Path(i["site"]),
            Path(i["original"]),
            Path(i["replacement"]),
            tuple(i["packages"]),
            i["import_subdirectory"],
        )
        for i in values["imports"]
    )
    return PreparationInputs(**values)


def boundary_registration(handle: BoundaryHandle) -> dict[str, Any]:
    import os

    from oversight.workspace.preview_registry import identity_data

    processes = {name: identity_data(identity.pid) for name, identity in handle.processes.items()}
    for name, identity in handle.processes.items():
        if int(processes[name]["birth"]) != identity.start_time:
            raise ValueError("stale authenticated boundary birth")
    links = {
        key: os.readlink(f"/proc/{handle.processes['init'].pid}/ns/{name}")
        for key, name in (("net", "net"), ("pid", "pid"), ("mnt", "mnt"))
    }
    witnesses = {
        key: (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for key, fd in handle.namespace_fds.items()
    }
    return dict(
        unshare=processes["outer"],
        namespace=processes["init"],
        namespaces=links,
        boundary=dict(processes=processes, namespaces=witnesses),
        boundary_pending=False,
        phase="confirmed",
    )


class AggregateScope:
    """Use existing supervisor-side delegation, or disclose per-process enforcement."""

    def __init__(self) -> None:
        self.path: Path | None = None

    def prepare(self, name: str, memory: int, cores: int) -> None:
        import os

        try:
            membership = Path("/proc/self/cgroup").read_text().splitlines()
            relative = next(row[3:] for row in membership if row.startswith("0::"))
            parent = Path("/sys/fs/cgroup") / relative.lstrip("/")
            if not {"cpu", "memory"} <= set(
                (parent / "cgroup.subtree_control").read_text().split()
            ):
                return
            directory = parent / name
            directory.mkdir()
            self.path = directory
            (directory / "memory.max").write_text(str(memory))
            (directory / "memory.swap.max").write_text("0")
            (directory / "cpu.max").write_text(f"{cores * 100000} 100000")
            if not os.access(directory / "cgroup.procs", os.W_OK):
                raise OSError("cgroup membership is not writable")
        except (OSError, ValueError, StopIteration):
            self.close()

    def attach(self, handle: BoundaryHandle) -> None:
        if self.path is not None:
            for identity in handle.processes.values():
                (self.path / "cgroup.procs").write_text(str(identity.pid))

    def close(self) -> None:
        if self.path is not None:
            self.path.rmdir()
            self.path = None


class ReviewedExecution:
    """Supervisor ownership spans preparation, builds, publication, service and verified drain."""

    def __init__(
        self,
        config: dict[str, Any],
        registry: PreviewRegistry,
        reader: asyncio.StreamReader,
        listener: socket.socket,
        send: Callable[..., None],
    ) -> None:
        import time

        self.config, self.registry, self.reader = config, registry, reader
        self.listener, self.send = listener, send
        self.reservation = listener.dup()
        self.inputs = preparation_inputs(config["reviewed"]["inputs"])
        self.spec = config["reviewed"]
        self.policy = self.spec["policy"]
        self.lease, self.absolute = config["lease_deadline"], config["absolute_deadline"]
        self.execution_deadline = min(
            self.absolute,
            time.monotonic()
            + sum(c["timeout_s"] for c in self.spec["commands"][:-1])
            + config["readiness_s"],
        )
        self.execution_deadline = min(
            self.execution_deadline, config.get("execution_deadline", self.execution_deadline)
        )
        self.execute = asyncio.Event()
        self.stopped = asyncio.Event()
        self.handle: BoundaryHandle | None = None
        self.gateway: PreviewGateway | None = None
        self.publication: PublishedBuild | None = None
        self.prepared: PreparedPreview | None = None
        from oversight.workspace.preview_supervisor import ListenerGuardian

        self.guardian = ListenerGuardian(registry)
        self.scope = AggregateScope()
        self.authorized = False
        self.build_command = False
        self.phase = "build"
        self.reason = "launch_failed"
        self.exit_code: int | None = None
        self.preparation_s = 0.0
        self.execution_reported = False
        self.started_gateway = False
        self.entry_uncertain = False
        self.log_text = ""
        self.logged: set[int] = set()
        self.log_secrets: tuple[str, ...] = ()

    def retain(self, handle: BoundaryHandle) -> None:
        self.handle = handle

    async def confirm(self, handle: BoundaryHandle) -> None:
        import time

        # Retain the borrowed authenticated handle before any cancellable persistence.
        self.handle = handle
        registration = boundary_registration(handle)
        await offload(lambda: self.registry.update(**registration))
        if self.guardian.transport is None:
            await self.guardian.start(self.reservation)
        await self.guardian.track(
            tuple(handle.pidfds[name] for name in ("outer", "setup", "init")),
            registration["boundary"]["processes"],
        )
        await offload(lambda: self.scope.attach(handle))
        await offload(lambda: self.registry.update(aggregate_enforced=self.scope.path is not None))
        if not self.authorized:
            self.send(kind="confirmed")
            execute = asyncio.create_task(self.execute.wait())
            stopped = asyncio.create_task(self.stopped.wait())
            try:
                await asyncio.wait({execute, stopped}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in (execute, stopped):
                    waiter.cancel()
                await asyncio.gather(execute, stopped, return_exceptions=True)
            if self.stopped.is_set() or time.monotonic() >= min(self.lease, self.absolute):
                raise ValueError("reviewed execution no longer authorized")
            self.authorized = True
        await offload(
            lambda: self.registry.update(phase="building" if self.build_command else "executing")
        )

    async def command(self, command: dict[str, Any], *, build: bool) -> None:
        import json
        import time

        from oversight.workspace.preview_boundary import BoundaryConfig, launch_boundary
        from oversight.workspace.preview_preparation import scrub_preview_env

        assert self.prepared is not None
        prepared = self.prepared
        self.build_command = build
        env = scrub_preview_env({}, self.prepared)
        payload = dict(
            argv=command["argv"],
            cwd=command["cwd"],
            env=env,
            python=command["argv"][0]
            in {str(t.path) for t in self.prepared.tools if t.name in {"python", "uv-python"}},
            python_bytes=self.policy["memory_limit_bytes"],
            node_heap_mib=self.policy["node_heap_mib"],
            cores=self.policy["build_cores" if build else "nested_cores"],
            file_bytes=self.policy["output_file_bytes"],
        )
        data = self.registry.read()
        if data is None:
            raise ValueError("missing reviewed intent")
        self.scope.prepare(
            "oversight-" + data["preview_id"],
            self.policy["build_memory_bytes" if build else "nested_memory_bytes"],
            payload["cores"],
        )
        await offload(
            lambda: self.registry.update(
                phase="between_commands" if self.authorized else "entering", boundary_pending=True
            )
        )
        roots = await offload(lambda: boundary_roots(prepared, self.inputs.excluded_roots))
        previous = self.handle
        try:
            handle = await launch_boundary(
                BoundaryConfig(
                    argv=("/opt/python/bin/python", "-I", "-S", "-c", _EXEC, json.dumps(payload)),
                    cwd=command["cwd"],
                    launch_timeout_s=self.config["readiness_s"],
                    lifetime_s=max(0.001, self.absolute - time.monotonic()),
                    output_cap=self.policy["diagnostics_bytes"],
                ),
                roots,
                before_exec=self.confirm,
                on_ownership=self.retain,
            )
        except BaseException:
            if self.handle is previous:
                # No authenticated ownership callback was delivered. Never use a prior
                # command's death as proof for this attempt, including bare leaf cleanup errors.
                self.entry_uncertain = True
            raise
        self.handle = handle
        if not self.execution_reported:
            self.send(kind="executing")
            self.execution_reported = True
        if build:
            async with asyncio.timeout(command["timeout_s"]):
                self.exit_code = await handle.wait()
            await self.write_log()
            self.scope.close()
            if self.exit_code != 0:
                raise ValueError("reviewed build failed")
        else:
            self.exit_code = None

    async def write_log(self) -> None:
        from oversight.workspace.preview_build_log import SanitizedBuildLog

        if self.handle is not None and self.prepared is not None:
            # The sink redacts and strips controls; none of these bytes enter lifecycle events.
            handle = self.handle
            log = SanitizedBuildLog(
                self.prepared.build_log,
                secrets=self.log_secrets,
                max_bytes=self.policy["diagnostics_bytes"],
            )
            capture = handle.stdout + handle.stderr
            await offload(lambda: log.write(self.log_text + capture))
            if handle.verified_dead and id(handle) not in self.logged:
                self.log_text = (self.log_text + capture)[-self.policy["diagnostics_bytes"] :]
                self.logged.add(id(handle))

    def backend_alive(self) -> bool:
        import select

        return bool(
            self.handle
            and not self.handle.verified_dead
            and set(self.handle.pidfds) == {"outer", "setup", "init"}
            and set(self.handle.namespace_fds) == {"u0", "n0", "u1", "pid", "mount"}
            and not any(select.select([fd], [], [], 0)[0] for fd in self.handle.pidfds.values())
        )

    async def startup(self) -> None:
        import time

        started = time.monotonic()
        from oversight.workspace.preview_types import preserve_ownership

        async def prepare_owned() -> None:
            self.prepared = await prepare_reviewed(self.inputs)

        await preserve_ownership(prepare_owned())
        self.preparation_s = time.monotonic() - started
        assert self.prepared is not None
        prepared = self.prepared
        await offload(
            lambda: self.registry.update(
                preparation_s=self.preparation_s,
                prepared_cleanup=prepared_cleanup(prepared, self.inputs),
            )
        )
        for command in self.spec["commands"][:-1]:
            await self.command(command, build=True)
        self.phase = "launch"
        await self.command(self.spec["commands"][-1], build=False)
        await self.attach_gateway()

    async def attach_gateway(self) -> None:
        import time

        from oversight.workspace.preview_gateway import PreviewGateway
        from oversight.workspace.preview_publication import OutputBudgets, publish_build

        assert self.prepared is not None and self.handle is not None
        self.publication = await publish_build(
            self.prepared.state / "dist",
            self.inputs.preview_directory / "published" / "build",
            tested_tree=self.prepared.tree,
            budgets=OutputBudgets(
                file_bytes=self.policy["output_file_bytes"],
                total_bytes=self.policy["output_total_bytes"],
                files=self.policy["output_files"],
                entries=max(20_000, self.policy["output_files"] * 2),
            ),
        )
        host = opposite_host(self.spec["parent_authority"].split(":")[0])
        self.gateway = PreviewGateway(
            self.listener,
            self.publication,
            BoundaryConnector(self.handle, self.spec["api_port"]),
            public_authority=f"{host}:{self.spec['public_port']}",
            api_authority=f"127.0.0.1:{self.spec['api_port']}",
            parent_origins=("http://" + self.spec["parent_authority"],),
            expected_run=self.spec["run_id"],
            backend_alive=self.backend_alive,
        )
        await self.gateway.start()
        self.started_gateway = True
        paths = self.publication.paths
        assets = tuple("/" + p for p in paths if p.startswith("assets/") and "worker" not in p)
        workers = tuple("/" + p for p in paths if "worker" in p)
        self.phase = "readiness"
        self.send(kind="probing")
        gateway = self.gateway
        deadline = min(self.execution_deadline, time.monotonic() + self.config["readiness_s"])
        while True:
            if not gateway.alive():
                self.reason = "app_exited"
                self.stopped.set()
                raise ValueError("reviewed service died during readiness")
            if time.monotonic() >= deadline:
                raise TimeoutError("reviewed readiness expired")
            try:
                evidence = await gateway.probe(
                    assets=assets,
                    workers=workers,
                    within_s=max(0.001, deadline - time.monotonic()),
                )
                break
            except (OSError, ValueError, TimeoutError):
                await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        nonce = self.gateway.authorize_ready(evidence, expected_run=self.spec["run_id"])
        self.send(kind="ready", nonce=nonce)

    async def controls(self) -> None:
        import json
        import time

        while not self.stopped.is_set():
            raw = await self.reader.readline()
            if raw == b"execute\n":
                self.execute.set()
            elif not raw or raw == b"stop\n":
                self.reason = "closed" if raw else "shutdown"
                self.stopped.set()
                return
            else:
                value = json.loads(raw)
                if "log_secrets" in value:
                    if (
                        self.authorized
                        or not isinstance(value["log_secrets"], list)
                        or any(not isinstance(secret, str) for secret in value["log_secrets"])
                    ):
                        raise ValueError("invalid log redaction configuration")
                    self.log_secrets = tuple(value["log_secrets"])
                    continue
                if time.monotonic() < min(self.lease, self.absolute):
                    self.lease = min(float(value["renew"]), self.absolute)

    async def monitor(self) -> None:
        import time

        while not self.stopped.is_set():
            if (
                self.guardian.transport is not None
                and self.guardian.transport.get_returncode() is not None
            ):
                self.reason = "app_exited"
                self.stopped.set()
                return
            if time.monotonic() >= min(self.lease, self.absolute):
                self.reason = (
                    "lifetime_expired" if time.monotonic() >= self.absolute else "lease_expired"
                )
                self.stopped.set()
                return
            if self.started_gateway and self.gateway is not None and not self.gateway.alive():
                self.reason = "app_exited"
                self.stopped.set()
                return
            if self.handle is not None and self.prepared is not None and self.backend_alive():
                handle, prepared, phase = self.handle, self.prepared, self.phase
                try:
                    exceeded = await offload(
                        partial(self.resource_exceeded, handle, prepared, phase)
                    )
                except (OSError, ValueError):
                    exceeded = True
                if exceeded:
                    self.reason = "memory_limit"
                    self.stopped.set()
                    return
            await asyncio.sleep(0.05)

    def resource_exceeded(
        self, handle: BoundaryHandle, prepared: PreparedPreview, phase: str
    ) -> bool:
        import os

        if cache_exceeded(prepared.state / "cache", self.policy["cache_bytes"]):
            return True
        # RSS is sampled per process, never advertised as an aggregate fork limit.
        pending_pids = [handle.processes["init"].pid]
        seen: set[int] = set()
        limit = self.policy["build_memory_bytes" if phase == "build" else "nested_memory_bytes"]
        while pending_pids:
            pid = pending_pids.pop()
            if pid in seen:
                continue
            seen.add(pid)
            if len(seen) > 10_000:
                return True
            try:
                rss = int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf(
                    "SC_PAGE_SIZE"
                )
                if rss > limit:
                    return True
                pending_pids.extend(
                    map(int, Path(f"/proc/{pid}/task/{pid}/children").read_text().split())
                )
            except (FileNotFoundError, ProcessLookupError):
                pass
        return False

    async def run(self) -> None:
        from oversight.workspace.preview_types import preserve_ownership

        controls = asyncio.create_task(self.controls())
        monitor = asyncio.create_task(self.monitor())
        startup = asyncio.create_task(self.startup())
        stop = asyncio.create_task(self.stopped.wait())
        try:
            done, _ = await asyncio.wait(
                {startup, stop, controls, monitor},
                timeout=max(0, self.execution_deadline - asyncio.get_running_loop().time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if startup in done:
                await startup
                await asyncio.wait({stop, controls, monitor}, return_when=asyncio.FIRST_COMPLETED)
            elif not done:
                self.reason = "readiness_timeout" if self.phase == "readiness" else "launch_failed"
        except asyncio.CancelledError:
            self.reason = "shutdown"
        except Exception as error:
            if not self.stopped.is_set():
                self.reason = (
                    "readiness_timeout"
                    if isinstance(error, TimeoutError) and self.phase == "readiness"
                    else "launch_failed"
                )
        finally:
            # Unblock the shielded boundary's before_exec hook before settling startup.
            self.stopped.set()
            for task in (startup, controls, monitor, stop):
                task.cancel()
            await preserve_ownership(
                asyncio.gather(startup, controls, monitor, stop, return_exceptions=True)
            )
            await preserve_ownership(self.cleanup())

    async def cleanup(self) -> None:
        try:
            if self.entry_uncertain:
                raise ValueError("boundary entry cleanup is unverified")
            if self.handle is not None:
                await self.handle.stop_verified()
                if not self.handle.verified_dead:
                    raise ValueError("boundary death is unverified")
            await self.guardian.close()
            await self.write_log()
            self.scope.close()
            if self.gateway is not None:
                await self.gateway.close()
            elif self.publication is not None:
                await offload(self.publication.close)
            if self.prepared is not None:
                prepared = self.prepared
                await offload(lambda: retire_prepared(prepared, self.inputs))
            self.listener.close()
            self.reservation.close()
            await offload(lambda: self.registry.update(phase="dead", boundary_pending=False))
            self.send(kind="dead", reason=self.reason, exit_code=self.exit_code, diagnostic="")
        except BaseException:
            # Keep the listener, identities and registry on uncertain death. Recovery must
            # retain quarantine; no unknown PID or listener authorizes reclamation.
            self.send(kind="unverified")


class BoundaryConnector:
    def __init__(self, handle: BoundaryHandle, api_port: int) -> None:
        from oversight.workspace.preview_connector import ConnectorTarget

        self.target = ConnectorTarget(handle.pidfds["outer"], handle.pidfds["outer"], api_port)

    async def connect_api(self) -> socket.socket:
        from oversight.workspace.preview_connector import connect_api

        return await connect_api(self.target, timeout_s=2)


def opposite_host(parent: str) -> str:
    if parent not in {"localhost", "127.0.0.1"}:
        raise ValueError("preview parent host is not allowed")
    return "127.0.0.1" if parent == "localhost" else "localhost"


def cache_exceeded(root: Path, limit: int) -> bool:
    """No-follow FD traversal: a child-controlled cache link cannot redirect host IO."""
    import os
    import stat

    try:
        pending = [os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)]
    except FileNotFoundError:
        return False
    size, entries = 0, 0
    try:
        while pending:
            fd = pending.pop()
            try:
                with os.scandir(fd) as paths:
                    for entry in paths:
                        info = entry.stat(follow_symlinks=False)
                        entries += 1
                        if entries > 100_000:
                            return True
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(
                                os.open(
                                    entry.name,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=fd,
                                )
                            )
                        elif stat.S_ISREG(info.st_mode):
                            size += info.st_size
                            if size > limit:
                                return True
            finally:
                os.close(fd)
        return False
    finally:
        for fd in pending:
            os.close(fd)


def prepared_cleanup(prepared: PreparedPreview, inputs: PreparationInputs) -> list[dict[str, Any]]:
    import os
    import stat

    records: list[dict[str, Any]] = []
    originals = {i.name: i.root for i in inputs.inventories}
    parents = {i.root.parent for i in prepared.manifests if i.root != originals[i.name]}
    storage = inputs.preparation_storage.resolve(strict=True)
    anchor = storage.stat()
    for parent in parents:
        info = parent.lstat()
        if (
            parent.resolve(strict=True) != parent
            or parent.parent != storage
            or not parent.name.startswith("preview-")
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("ambiguous prepared copy ownership")
        records.append(
            dict(
                path=str(parent),
                storage=str(storage),
                token=[info.st_dev, info.st_ino],
                anchor=[anchor.st_dev, anchor.st_ino],
            )
        )
    return records


def retire_registered(records: list[dict[str, Any]]) -> None:
    import os
    import shutil
    import stat

    for record in records:
        if set(record) != {"path", "storage", "token", "anchor"} or any(
            not isinstance(record[name], list)
            or len(record[name]) != 2
            or any(type(value) is not int or value <= 0 for value in record[name])
            for name in ("token", "anchor")
        ):
            raise ValueError("invalid prepared copy ownership")
        parent, storage = Path(record["path"]), Path(record["storage"])
        if (
            storage.resolve(strict=True) != storage
            or parent.parent != storage
            or not parent.name.startswith("preview-")
        ):
            raise ValueError("ambiguous prepared copy ownership")
        anchor = storage.stat()
        if [anchor.st_dev, anchor.st_ino] != record["anchor"]:
            raise ValueError("prepared storage identity changed")
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue  # Verified cleanup may have completed before the prior backend died.
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or [info.st_dev, info.st_ino] != record["token"]
        ):
            raise ValueError("prepared copy identity changed")
        shutil.rmtree(parent)


def retire_prepared(prepared: PreparedPreview, inputs: PreparationInputs) -> None:
    """Retire this call's fresh import copies only after verified descendant/gateway death."""
    retire_registered(prepared_cleanup(prepared, inputs))
