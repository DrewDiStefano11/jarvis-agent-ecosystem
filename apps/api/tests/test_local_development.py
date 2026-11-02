"""Default-off loopback proposals; never live model quality or sandbox proof."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.development_agent.config import DevelopmentProviderSettings, build_development_router
from app.development_agent.execution import DevelopmentExecutor
from app.model_providers.contracts import ModelCapability
from app.model_providers.ollama import OllamaProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter
from app.models.development_dispatch import DevelopmentLimits

pytest_plugins = ["tests.test_development_agent"]

CAPABILITIES = frozenset(
    {
        ModelCapability.CHAT,
        ModelCapability.CODE_GENERATION,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.TOOL_CALLING,
    }
)


def local_configuration(**changes):
    values = dict(enabled=True, provider="local", model="coding-model", _env_file=None)
    values.update(changes)
    return DevelopmentProviderSettings(**values)


def planning_router(**changes):
    values = dict(
        default_model="planning-model", execution_mode="local_only", capabilities=CAPABILITIES
    )
    values.update(changes)
    provider = OllamaProvider(**values)
    return ModelRouter(ProviderRegistry([provider]), RetryExecutor(RetryPolicy(maximum_attempts=3)))


def test_local_factory_is_separate_and_has_no_physical_retry_or_client_creation(monkeypatch):
    import httpx

    existing = planning_router()
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *a, **kw: pytest.fail("factory attempted network")
    )
    router = build_development_router(local_configuration(), existing)
    assert router is not None
    assert router is not existing and router.registry is not existing.registry
    assert router.retry.policy.maximum_attempts == 1
    assert existing.retry.policy.maximum_attempts == 3
    selected = router.registry.list()
    assert len(selected) == 1
    assert selected[0].is_local and selected[0].execution_mode == "local_only"
    assert selected[0].default_model == "coding-model"
    assert existing.registry.list()[0].default_model == "planning-model"
    assert ModelCapability.TOOL_CALLING not in selected[0].capabilities


@pytest.mark.parametrize("provider", ["openai", "codex_app_server"])
def test_hosted_development_stays_quarantined(provider):
    assert (
        build_development_router(local_configuration(provider=provider), planning_router()) is None
    )


@pytest.mark.parametrize("change", ["disabled", "remote", "no_code", "no_schema", "inactive_mode"])
def test_unavailable_local_configuration_stays_closed(change):
    settings = local_configuration(enabled=change != "disabled")
    kwargs = {}
    if change == "remote":
        kwargs["base_url"] = "https://models.example.invalid"
    elif change == "no_code":
        kwargs["capabilities"] = CAPABILITIES - {ModelCapability.CODE_GENERATION}
    elif change == "no_schema":
        kwargs["capabilities"] = CAPABILITIES - {ModelCapability.STRUCTURED_OUTPUT}
    elif change == "inactive_mode":
        kwargs["execution_mode"] = "disabled"
    assert build_development_router(settings, planning_router(**kwargs)) is None


def test_local_limits_endpoint_is_bound_without_changing_old_serialized_shape():
    values = dict(
        provider="ollama", model="coding-model", completion_criteria=("Keep the source contract.",)
    )
    original = DevelopmentLimits(**values)
    assert "local_endpoint" not in original.model_dump(mode="json")
    bound = DevelopmentLimits(**values, local_endpoint="http://127.0.0.1:11434")
    assert bound.local_endpoint == "http://127.0.0.1:11434"
    assert bound.model_dump(mode="json")["local_endpoint"] == bound.local_endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.invalid",
        "http://user:private@127.0.0.1:11434",
        "file:///tmp/model",
        "ftp://127.0.0.1:11434",
        "http://127.0.0.1:notaport",
        "http://127.0.0.1:70000",
        "http://127.0.0.1:0",
        "http://127.0.0.1/private",
        "http://127.0.0.1:11434?key=private",
        "http://127.0.0.1:11434#fragment",
    ],
)
def test_invalid_local_admission_endpoints_are_rejected(endpoint):
    with pytest.raises(ValidationError):
        DevelopmentLimits(
            provider="ollama",
            model="coding-model",
            completion_criteria=("Keep source intact.",),
            local_endpoint=endpoint,
        )


def test_executor_checks_current_endpoint_against_independent_admission():
    settings = local_configuration()
    router = build_development_router(settings, planning_router())
    service = object.__new__(DevelopmentExecutor)
    service.settings, service.router = settings, router
    limits = SimpleNamespace(
        provider="ollama", model="coding-model", local_endpoint="http://127.0.0.1:11434"
    )
    root = SimpleNamespace(plan=SimpleNamespace(limits=limits))
    assert service.route(root).allow_remote is False
    limits.local_endpoint = "http://127.0.0.1:11435"
    with pytest.raises(DomainError) as error:
        service.route(root)
    assert error.value.code == "DEVELOPMENT_PROVIDER_CHANGED"


@pytest.mark.asyncio
@pytest.mark.parametrize("health_stop", [False, True])
async def test_real_fixture_http_proposal_requires_native_source_approval_and_replays(
    development, health_stop
):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.development_agent.dispatch import DevelopmentDispatchService
    from app.models.source_mutation import ApplySourceMutation, ApproveSourceMutation
    from tests.test_development_agent import proposal
    from tests.test_development_execution import grant_editing

    app, actor, operator, authority, _, target, _, context = development
    calls = []

    class FixtureOllama(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if health_stop:
                from app.db.models import SystemStateRow

                with app.state.repository.session_factory() as session, session.begin():
                    session.get(SystemStateRow, 1).emergency_stop = True
            self.reply({"models": [{"name": "coding-model"}]})

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply(
                {
                    "model": "coding-model",
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 100,
                    "eval_count": 200,
                    "message": {
                        "role": "assistant",
                        "content": proposal(context).model_dump_json(),
                    },
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        settings = local_configuration()
        service = DevelopmentExecutor(
            app, settings, build_development_router(settings, planning_router(base_url=endpoint))
        )
        policy = DevelopmentLimits(
            provider="ollama",
            model="coding-model",
            local_endpoint=endpoint,
            completion_criteria=("The accessor returns the configured value.",),
        )
        dispatches = DevelopmentDispatchService(app)
        preview = dispatches.preview(
            actor, context.workspace_id, authority, context.selection, policy
        )
        admitted = dispatches.admit(
            operator,
            actor,
            context.workspace_id,
            authority,
            context.selection,
            policy,
            preview.plan_hash,
        )
        assert admitted.plan.limits.local_endpoint == endpoint
        args = (actor, context.workspace_id, authority, context.selection, "local-fixture-proposal")
        if health_stop:
            with pytest.raises(DomainError):
                await service.execute(*args)
            assert not calls
            assert (target / "source.py").read_bytes() == b"value = 1\n"
            assert not (target / "new.py").exists()
            return
        result = await service.execute(*args)
        assert result.state == "awaiting_source_approval" and len(calls) == 1
        assert calls[0]["model"] == "coding-model" and "tools" not in calls[0]
        assert authority.lease_token not in json.dumps(calls)
        assert context.context_hash in json.dumps(calls)
        assert (target / "source.py").read_bytes() == b"value = 1\n" and not (
            target / "new.py"
        ).exists()
        assert await service.execute(*args) == result
        assert len(calls) == 1
        _, editing = dispatches.editing(actor, context.workspace_id, authority, result.dispatch_id)
        grant_editing(app, actor, operator)
        mutation_plan = service.mutations.preview(actor, editing.record_id)
        approval = service.mutations.approve(
            operator,
            ApproveSourceMutation(
                editing_record_id=editing.record_id, expected_plan_hash=mutation_plan.plan_hash
            ),
        )
        request = ApplySourceMutation(
            editing_record_id=editing.record_id,
            expected_plan_hash=mutation_plan.plan_hash,
            approval_id=approval.approval_id,
            worker_id=authority.worker_id,
            lease_token=authority.lease_token,
        )
        with pytest.raises(DomainError):
            service.apply_approved(
                actor,
                context.workspace_id,
                authority,
                result.dispatch_id,
                request.model_copy(update={"approval_id": "foreign"}),
            )
        assert (target / "source.py").read_bytes() == b"value = 1\n"
        mutation = service.apply_approved(
            actor, context.workspace_id, authority, result.dispatch_id, request
        )
        assert mutation.state == "complete"
        assert (target / "source.py").read_bytes() == b"value = 2\n"
        assert (target / "new.py").read_bytes().startswith(b"from source import value")
        assert (await service.execute(*args)).state == "mutation_recorded" and len(calls) == 1
        handoff = service.handoff(
            actor, context.workspace_id, authority, result.dispatch_id, request
        )
        assert not handoff.validation_passed and not handoff.publication_ready
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("change", ["disabled", "hosted", "mode", "tools", "retry", "endpoint"])
def test_executor_live_configuration_changes_never_reuse_local_authority(change):
    settings = local_configuration()
    router = build_development_router(settings, planning_router())
    service = object.__new__(DevelopmentExecutor)
    service.settings, service.router = settings, router
    limits = DevelopmentLimits(
        provider="ollama",
        model="coding-model",
        local_endpoint="http://127.0.0.1:11434",
        completion_criteria=("Preserve authority.",),
    )
    if change == "disabled":
        settings.enabled = False
    elif change == "hosted":
        settings.provider = "openai"
    elif change == "mode":
        router.registry.list()[0].execution_mode = "free_only"
    elif change == "tools":
        router.registry.list()[0].capabilities |= {ModelCapability.TOOL_CALLING}
    elif change == "retry":
        router.retry = RetryExecutor(RetryPolicy(maximum_attempts=2))
    elif change == "endpoint":
        router.registry.list()[0].base_url = "http://127.0.0.1:11435"
    with pytest.raises(DomainError) as error:
        service.route(SimpleNamespace(plan=SimpleNamespace(limits=limits)))
    assert error.value.code in {"DEVELOPMENT_EXECUTION_DISABLED", "DEVELOPMENT_PROVIDER_CHANGED"}


def test_invalid_local_endpoint_errors_never_echo_untrusted_port():
    private = "private-port-fixture-value"
    with pytest.raises(ValidationError) as error:
        DevelopmentLimits(
            provider="ollama",
            model="coding-model",
            local_endpoint=f"http://127.0.0.1:{private}",
            completion_criteria=("Keep source unchanged.",),
        )
    assert private not in str(error.value)


@pytest.mark.parametrize("model", ["coding-cloud", "coding:cloud", "coding:CLOUD"])
def test_cloud_model_identifiers_never_enable_development(model):
    settings = local_configuration(model=model)
    assert build_development_router(settings, planning_router()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disabled", "endpoint", "model"])
async def test_revocation_during_provider_health_prevents_inference_post(change):
    import httpx

    from tests.test_development_provider import request

    settings = local_configuration(timeout_seconds=3)
    calls = []
    selected = None

    def transport(wire):
        calls.append((wire.method, str(wire.url)))
        if len(calls) == 1:
            if change == "disabled":
                settings.enabled = False
            elif change == "endpoint":
                selected.base_url = "http://127.0.0.1:11435"
            else:
                settings.model = "another-model"
        if wire.method == "GET":
            return httpx.Response(200, json={"models": [{"name": "coding-model"}]})
        return httpx.Response(
            200,
            json={
                "model": "coding-model",
                "message": {"role": "assistant", "content": "{}"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 1,
                "eval_count": 1,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(transport), trust_env=False
    ) as client:
        router = build_development_router(settings, planning_router(client=client))
        selected = router.registry.list()[0]
        root = SimpleNamespace(
            plan=SimpleNamespace(
                limits=SimpleNamespace(
                    provider="ollama", model="coding-model", local_endpoint="http://127.0.0.1:11434"
                )
            )
        )
        record = SimpleNamespace(
            reservation=SimpleNamespace(request=request(), dispatch_id="fixture-dispatch")
        )
        service = object.__new__(DevelopmentExecutor)
        service.settings, service.router = settings, router
        service.dispatches = SimpleNamespace(remaining_seconds=lambda _: 5)
        service.monitor_interval_seconds = 1

        def check(*args, **kwargs):
            service.route(root)
            return root, None

        service.check = check
        with pytest.raises(DomainError) as error:
            await service.call(None, "workspace", None, record, root)
        assert error.value.code in {
            "DEVELOPMENT_EXECUTION_DISABLED",
            "DEVELOPMENT_PROVIDER_CHANGED",
        }
        assert calls and all(method == "GET" for method, _ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local_only", "free_only"])
@pytest.mark.parametrize("model", ["coding-cloud", "coding:cloud", "coding:CLOUD"])
async def test_native_ollama_cloud_names_are_refused_before_any_http_client(
    monkeypatch, mode, model
):
    import httpx

    from app.model_providers.errors import ProviderExecutionDisabledError
    from tests.test_development_provider import request

    def forbidden(*args, **kwargs):
        pytest.fail("Cloud identifier must be denied before HTTP construction")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    engine = OllamaProvider(default_model=model, execution_mode=mode, capabilities=CAPABILITIES)
    with pytest.raises(ProviderExecutionDisabledError):
        await engine.execute(request(model=model))
