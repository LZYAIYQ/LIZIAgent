"""FastAPI entry point for LZAgent."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from . import __version__
from .agent.loop import AgentLoop
from .api import confirmations as confirmations_api
from .api import cron as cron_api
from .api import curator as curator_api
from .api import delivery_targets as delivery_targets_api
from .api import doctor as doctor_api
from .api import gateways as gateways_api
from .api import plugins as plugins_api
from .api import health as health_api
from .api import llm as llm_api
from .api import mcp as mcp_api
from .api import graph_rag as graph_rag_api
from .api import knowledge_bases as knowledge_bases_api
from .api import maintenance as maintenance_api
from .api import memory as memory_api
from .api import nightly as nightly_api
from .api import runtime as runtime_api
from .api import review as review_api
from .api import sessions as sessions_api
from .api import skills as skills_api
from .api import tools as tools_api
from .api import users as users_api
from .api import weixin as weixin_api
from .api import wiki as wiki_api
from .core.config import get_settings
from .core.logging import configure_logging
from .cron.drafts import CronDraftStore
from .cron.deliverer import CronDeliverer
from .cron.scheduler import CronScheduler
from .db.models import (
    CronJob,
    DeliveryTarget as DeliveryTargetRow,
    apply_lightweight_migrations,
    create_all,
)
from .db.session import SessionLocal, engine, session_scope
from .gateways.base import DeliveryTarget, IncomingMessage, OutgoingMessage
from .gateways.manager import GatewayManager
from .gateways.webhook import WebhookGateway, build_router as build_webhook_router
from .gateways.wecom_bot import WeComBotGateway
from .gateways.weixin import WeixinGateway
from .gateways.feishu import FeishuGateway, build_feishu_router
from .graph.nightly import NightlyGraphPipeline
from .graph.scheduler import KnowledgeGraphScheduler
from .graph.source import collect_graph_records
from .memory.maintenance import MemoryMaintenance
from .harness import build_harness
from .harness.accelerate.tool_memo import ToolMemo, attach_tool_memo
from .harness.extensions import api as harness_api
from .harness.observability import api as harness_obs_api
from .harness.observability.tracer import TraceRecorder, attach_tracer
from .harness.progress import (
    ProgressEmitter,
    attach_progress,
    bind_sink as _progress_bind_sink,
    current_turn_has_emitted,
    unbind_sink as _progress_unbind_sink,
)
from .llm import LLMClient
from .agent.routing import should_ack_first as _policy_should_ack_first
from .bootstrap.llm import build_llm, build_router_llm
from .bootstrap.agent import build_agent, build_confirmation_store
from .skills.loader import SkillLoader
from .tools import ToolRegistry
from .tools.core_capabilities import (
    register_core_reminder_tools,
    register_core_web_query_tools,
)
from .tools.builtins import (
    CodeExecutionTool,
    DelegateTool,
    FileExtractTool,
    KnowledgeIngestTool,
    KnowledgeInspectTool,
    KnowledgeModeManageTool,
    MemoryManageTool,
    ReadFileTool,
    SendMessageTool,
    SkillManageTool,
    ToolSearchTool,
    WriteFileTool,
)

def _should_ack_first(message: IncomingMessage) -> bool:
    return _policy_should_ack_first(message)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    settings.ensure_directories()

    logger.info("starting {} v{}", settings.app_name, __version__)
    logger.info("data_dir={} config_dir={} workspace_dir={}",
                settings.data_dir, settings.config_dir, settings.workspace_dir)

    create_all(engine)
    apply_lightweight_migrations(engine)

    # v0.37.8 / optional Redis hot layer in front of session
    # context, travel_realtime bundle cache, and the wiki answer cache.
    # Helper handles the best-effort health check; ``None`` is the
    # disabled state every consumer falls back from.
    from .storage.bootstrap import build_redis_backend_with_healthcheck
    redis_backend = build_redis_backend_with_healthcheck(settings)
    app.state.redis_backend = redis_backend


    llm_client = build_llm(settings)
    router_llm = build_router_llm(settings, llm_client)

    # skill subsystem boot helper. Builds usage_store +
    # history + guard + loader + wiki_store + geo_store as one unit.
    from .skills.bootstrap import build_skill_subsystem
    skill_subsystem = build_skill_subsystem(
        settings,
        redis_backend=redis_backend,
        session_factory=SessionLocal,
    )
    usage_store = skill_subsystem.usage_store
    skill_history_store = skill_subsystem.skill_history_store
    skill_guard = skill_subsystem.skill_guard
    skill_loader = skill_subsystem.skill_loader
    wiki_store = skill_subsystem.wiki_store
    geo_store = skill_subsystem.geo_store

    # memory subsystem boot helper.
    from .memory.bootstrap import build_memory_subsystem
    memory_store, memory_manager = build_memory_subsystem(
        settings, redis_backend=redis_backend,
    )
    app.state.memory_manager = memory_manager

    # user manager for multi-user support.
    from .db.users import UserManager
    user_manager = UserManager()
    app.state.user_manager = user_manager

    # knowledge-graph LLM extractor + sidecar JSON cache. Synchronous
    # request paths only ever read the cache; cache writes happen on ingest
    # (background asyncio task) or on POST /api/knowledge-bases/{id}/rebuild-graph.
    from .graph.cache import LLMGraphCache
    from .graph.llm_extractor import LLMGraphExtractor
    if settings.graph_llm_enabled and llm_client is not None:
        graph_cache_dir = settings.workspace_dir / "graph_cache"
        graph_cache_dir.mkdir(parents=True, exist_ok=True)
        graph_extractor: Optional[LLMGraphExtractor] = LLMGraphExtractor(
            llm=llm_client,
            cache=LLMGraphCache(graph_cache_dir),
            timeout_seconds=settings.graph_llm_timeout_seconds,
            max_content_chars=settings.graph_llm_max_chars,
        )
    else:
        graph_extractor = None

    tool_registry = ToolRegistry()
    tool_registry.register(ToolSearchTool(tool_registry))
    register_core_web_query_tools(tool_registry)
    tool_registry.register_many(
        [
            ReadFileTool(settings.workspace_dir, usage_store=usage_store),
            WriteFileTool(settings.workspace_dir),
            FileExtractTool(settings.workspace_dir),
            SkillManageTool(
                settings.workspace_dir / "skills",
                usage_store=usage_store,
                history_store=skill_history_store,
                guard=skill_guard,
            ),
            KnowledgeIngestTool(settings.workspace_dir, graph_extractor=graph_extractor),
            KnowledgeInspectTool(settings.workspace_dir),
            KnowledgeModeManageTool(settings.workspace_dir),
            MemoryManageTool(
                memory_store, memory_manager=memory_manager,
                max_fact_chars=settings.memory_max_fact_chars,
            ),
            CodeExecutionTool(settings.workspace_dir),
        ]
    )
    register_core_reminder_tools(tool_registry, skill_loader=skill_loader)

    # Travel domain — pluggable knowledge pack. Comment out this block
    # to disable all travel-specific tools/routes/Redis cache wiring.
    from .domains.travel import register_travel_domain
    _travel_base_url = (
        f"http://{settings.host}:{settings.port}"
        if settings.host != "0.0.0.0"
        else f"http://localhost:{settings.port}"
    )
    register_travel_domain(
        app=app,
        tool_registry=tool_registry,
        wiki_store=wiki_store,
        geo_store=geo_store,
        redis_backend=redis_backend,
        bundle_ttl_seconds=settings.redis_bundle_ttl_seconds,
        base_url=_travel_base_url,
    )
    logger.info(
        "tool registry ready: {} tool(s) {} (travel_realtime bundle redis={})",
        len(tool_registry),
        tool_registry.counts_by_permission(),
        "on" if redis_backend is not None else "off",
    )

    confirmation_store = build_confirmation_store(settings)
    agent = build_agent(
        settings,
        llm_client=llm_client,
        router_llm=router_llm,
        tool_registry=tool_registry,
        confirmation_store=confirmation_store,
        skill_loader=skill_loader,
        memory_manager=memory_manager,
        memory_store=memory_store,
        wiki_store=wiki_store,
        geo_store=geo_store,
    )
    tool_registry.register(DelegateTool(agent))

    # v0.14 / MCP client subsystem. Boot helper lives in
    # ``backend/mcp/bootstrap.py``; it builds the manager, store,
    # lifecycle service, and registers the ``mcp_manage`` tool.
    # Returns ``(None, None, None)`` when ``mcp_enabled`` is False.
    from .mcp.bootstrap import build_mcp_subsystem
    mcp_manager, mcp_store, mcp_lifecycle = await build_mcp_subsystem(
        settings,
        tool_registry=tool_registry,
        session_local=SessionLocal,
    )

    # Harness facade. Built here — BEFORE route_message captures it —
    # so the gateway intercept can call into harness commands. Plugin
    # loader is wired later (after the manager + agent finish setup);
    # we patch it onto the live instance at that point.
    app.state.harness = build_harness(
        tool_registry=tool_registry,
        skill_loader=skill_loader,
        mcp_store=mcp_store,
        mcp_manager=mcp_manager,
        mcp_lifecycle=mcp_lifecycle,
        plugin_loader=None,
    )
    _harness = app.state.harness

    ack_background_tasks: set[asyncio.Task[None]] = set()

    async def _progress_send_to_gateway(
        target: DeliveryTarget, text: str
    ) -> None:
        """Sink used by the progress emitter — dispatches a short status ping.

        Wrapped in a try/except because a transient gateway failure
        should never crash the agent turn that's still in progress.
        """
        try:
            await manager.dispatch(OutgoingMessage(target=target, text=text))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[harness.progress] sink dispatch failed: {} (target={})",
                exc, target,
            )

    async def _delayed_thinking_ack(
        delay_seconds: float, target: DeliveryTarget
    ) -> None:
        """Send "🤔 想想这事儿…" only if the turn is still running and silent.

        Cancelled by ``_run_agent_and_dispatch`` once the real reply
        is on the wire; suppressed silently if any tool ping already
        fired by the time the sleep ends (avoids back-to-back
        "🔎 搜索中…" + "🤔 想想…" surprises).
        """
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        if current_turn_has_emitted():
            return
        emitter = getattr(app.state.harness, "progress", None)
        if emitter is None:
            return
        await emitter.phase_changed("starting")

    async def _run_agent_and_dispatch(
        message: IncomingMessage,
        *,
        already_acked: bool = False,
    ) -> None:
        # Phase 6 — bind a per-turn progress sink for this asyncio
        # task. Cron-driven runs / runs without ``reply_target`` skip
        # the wiring; the emitter degrades to a no-op there.
        progress_token = None
        ack_task: Optional[asyncio.Task[None]] = None
        emitter = getattr(app.state.harness, "progress", None)
        if message.reply_target is not None and emitter is not None:
            target = message.reply_target

            async def _sink(text: str) -> None:
                await _progress_send_to_gateway(target, text)

            progress_token = _progress_bind_sink(
                _sink, cooldown_seconds=emitter.default_cooldown_seconds,
            )
            if already_acked:
                # Ack-first already sent "好的，我来处理。" — suppress the
                # auto-thinking ack to avoid two leading messages, but
                # leave tool pings on (they're useful even after ack).
                emitter.disable_for_current_turn()
                # Re-enable tool pings: the disable flag is per-turn
                # context "enabled". Tool pings respect it. We *want*
                # tool pings, so flip it back on after acknowledging
                # the suppression was just for the thinking ack.
                from .harness.progress.emitter import _TURN_CTX as _PROGRESS_CTX
                _ctx = _PROGRESS_CTX.get()
                if _ctx is not None:
                    _ctx.enabled = True
            else:
                ack_task = asyncio.create_task(
                    _delayed_thinking_ack(3.0, target)
                )

        try:
            reply = await agent.run_turn(message, dispatch_fn=manager.dispatch)
            if reply is not None:
                await manager.dispatch(reply)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "background agent turn failed: platform={} user={} error={}",
                message.platform,
                message.user_id,
                exc,
            )
        finally:
            if ack_task is not None:
                ack_task.cancel()
                try:
                    await ack_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if progress_token is not None:
                _progress_unbind_sink(progress_token)

    async def route_message(message: IncomingMessage) -> None:
        # Harness slash-command intercept. Runs BEFORE the agent turn
        # so /list, /remove, /help short-circuit without spending an
        # LLM call. Unrecognised input falls through to the agent.
        text = (message.text or "").strip()
        if text.startswith("/"):
            try:
                harness_reply = await _harness.handle_command(text)
            except Exception as exc:  # noqa: BLE001 — never break IM
                logger.exception("[harness] command failed for {!r}: {}", text, exc)
                harness_reply = None
            if harness_reply is not None:
                await manager.dispatch(OutgoingMessage(
                    target=message.reply_target,
                    text=harness_reply,
                ))
                return

        if (
            _should_ack_first(message)
            and message.user_id
            and confirmation_store.find_pending_for(
                platform=message.platform,
                user_id=message.user_id,
            ) is None
        ):
            try:
                await manager.dispatch(
                    OutgoingMessage(
                        target=message.reply_target,
                        text="好的，我来处理。",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ack-first dispatch failed for platform={} user={}: {}",
                    message.platform,
                    message.user_id,
                    exc,
                )
            task = asyncio.create_task(
                _run_agent_and_dispatch(message, already_acked=True)
            )
            ack_background_tasks.add(task)
            task.add_done_callback(ack_background_tasks.discard)
            return
        await _run_agent_and_dispatch(message, already_acked=False)

    manager = GatewayManager(
        agent_handler=route_message,
        per_user_concurrency=settings.per_user_concurrency,
    )
    webhook_gateway = WebhookGateway(handler=manager.handle_incoming)
    manager.register(webhook_gateway)
    app.include_router(build_webhook_router(webhook_gateway))

    wecom_bot_gateway = WeComBotGateway(handler=manager.handle_incoming)
    manager.register(wecom_bot_gateway)

    weixin_gateway = WeixinGateway(handler=manager.handle_incoming)
    manager.register(weixin_gateway)

    # Feishu gateway (supports file attachments)
    feishu_gateway = FeishuGateway(handler=manager.handle_incoming)
    manager.register(feishu_gateway)
    if feishu_gateway.is_configured():
        app.include_router(build_feishu_router(feishu_gateway))

    # send_message_tool needs the gateway manager to dispatch
    # outbound. Register it after the manager is fully wired up so the
    # tool's confirm-tier flow always finds a real adapter to push
    # through (rather than crashing at execute() time).
    tool_registry.register(SendMessageTool(gateway_manager=manager))

    draft_store = CronDraftStore()
    cron_deliverer = CronDeliverer(manager.dispatch, draft_store)

    async def run_cron_job(job: CronJob) -> str:
        """Deliver a cron job through its DeliveryTarget.

        From v0.4: when the LLM is configured, ``job.instruction`` is treated
        as a directive and the agent loop generates the actual push text.
        Without an LLM the instruction is sent verbatim, preserving v0.2/v0.3
        behaviour for offline / smoke-test environments.

        From v0.10: if ``pre_script_path`` is set, the script is exec'd
        before the LLM is called and its stdout is spliced into the user
        message as ground-truth data. Failures are logged but do **not**
        abort dispatch — we surface the error in the eventual push so the
        user sees it (and the skill's own ``Failure Mode`` section can
        decide whether to ``[SILENT]`` or alert).
        """
        if job.delivery_target_id is None:
            raise RuntimeError(
                f"cron job '{job.name}' has no delivery_target_id; cannot deliver"
            )

        with session_scope() as session:
            target = session.get(DeliveryTargetRow, job.delivery_target_id)
            if target is None:
                raise RuntimeError(
                    f"delivery target {job.delivery_target_id} not found for job '{job.name}'"
                )
            if not target.enabled:
                raise RuntimeError(
                    f"delivery target {target.display_name or target.target_id} is disabled"
                )
            dto = DeliveryTarget(
                platform=target.platform,
                target_type=target.target_type,
                target_id=target.target_id,
                display_name=target.display_name,
            )

        pre_script_output: Optional[str] = None
        if job.pre_script_path:
            from .cron.pre_script import run_pre_script
            result = await run_pre_script(
                workspace_dir=settings.workspace_dir,
                script_path=job.pre_script_path,
                timeout_seconds=job.pre_script_timeout_seconds or 30,
                job_name=job.name,
            )
            if result.ok:
                logger.info(
                    "pre_script for cron '{}' ok in {}ms ({} chars{})",
                    job.name, result.duration_ms, len(result.stdout),
                    ", truncated" if result.truncated else "",
                )
                pre_script_output = result.stdout
            else:
                logger.warning(
                    "pre_script for cron '{}' failed: {} ({}ms)",
                    job.name, result.error, result.duration_ms,
                )
                pre_script_output = (
                    f"[pre_script failure] {result.error}\n"
                    f"(stdout captured before failure was {len(result.stdout)} chars)"
                )

        content = await agent.generate_cron_message(
            job.instruction or "",
            job_name=job.name,
            skill_hint=job.skill_hint,
            pre_script_output=pre_script_output,
        )
        from .cron.drafts import CronDraft

        draft = CronDraft(
            job_id=job.id,
            job_name=job.name,
            planned_fire_at=datetime.utcnow(),
            generated_at=datetime.utcnow(),
            content=content,
            meta={
                "delivery_target": dto.describe(),
                "lead_minutes": getattr(job, "lead_minutes", 2),
            },
        )
        draft_store.put(draft)

        last_error: Optional[str] = None
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                if attempt > 1:
                    logger.warning(
                        "cron job '{}' retrying dispatch attempt {}/{}",
                        job.name, attempt, max_attempts,
                    )
                await manager.dispatch(OutgoingMessage(target=dto, text=content))
                cron_deliverer.mark_delivered(job.id)
                logger.info(
                    "cron job '{}' delivered to {} ({} chars, llm={}, attempt={}/{})",
                    job.name,
                    dto.describe(),
                    len(content),
                    agent.llm_configured,
                    attempt,
                    max_attempts,
                )
                return "delivered"
            except Exception as exc:  # noqa: BLE001
                last_error = f"dispatch failed: {type(exc).__name__}: {exc}"
                draft_store.mark_failed(job.id, last_error)
                logger.warning(
                    "cron job '{}' dispatch attempt {}/{} failed: {}",
                    job.name,
                    attempt,
                    max_attempts,
                    last_error,
                )
                if attempt < max_attempts:
                    await asyncio.sleep(2 ** (attempt - 1))

        raise RuntimeError(last_error or "dispatch failed")

    async def _expire_pending_confirmations() -> None:
        # Wrap the sync expire_old call so the scheduler can await it; doing
        # work in a thread is unnecessary for our SQLite scale.
        confirmation_store.expire_old()

    cron_scheduler = CronScheduler(
        runner=run_cron_job,
        pre_tick_hook=_expire_pending_confirmations,
    )
    graph_scheduler = KnowledgeGraphScheduler(
        source_fn=collect_graph_records,
        export_dir=settings.workspace_dir / "graph",
        interval_seconds=24 * 60 * 60,
    )

    # skill curator service runs alongside the cron scheduler.
    from .skills.curator import SkillCurator
    from .skills.curator_service import SkillCuratorService
    skill_curator = SkillCurator(
        loader=skill_loader,
        usage_store=usage_store,
        stale_after_days=settings.skill_curator_stale_after_days,
        archive_after_days=settings.skill_curator_archive_after_days,
        dedupe_threshold=settings.skill_curator_dedupe_threshold,
    )
    curator_service = SkillCuratorService(
        skill_curator,
        warmup_seconds=settings.skill_curator_warmup_seconds,
        interval_seconds=settings.skill_curator_interval_seconds,
        enabled=settings.skill_curator_enabled,
    )

    # skill knowledge consolidator. Pure-file derived view of the
    # skill graph under ``workspace/knowledge/wiki/``. Cheap, runs on a
    # slow cadence; never edits authoritative SKILL.md content.
    from .skills.consolidator import SkillKnowledgeConsolidator
    from .skills.consolidator_service import SkillKnowledgeConsolidatorService
    knowledge_dir = settings.skill_knowledge_dir or (settings.workspace_dir / "knowledge")
    skill_knowledge_consolidator = SkillKnowledgeConsolidator(
        loader=skill_loader,
        knowledge_dir=knowledge_dir,
        workspace_dir=settings.workspace_dir,
        max_body_chars=settings.skill_knowledge_max_body_chars,
    )
    skill_knowledge_service = SkillKnowledgeConsolidatorService(
        skill_knowledge_consolidator,
        warmup_seconds=settings.skill_knowledge_warmup_seconds,
        interval_seconds=settings.skill_knowledge_interval_seconds,
        enabled=settings.skill_knowledge_enabled,
    )

    # Phase B — daily end-of-day review. Drives the
    # END_OF_DAY_REVIEW_PROMPT review fork once per day so the agent
    # consolidates the day's L2 agent_note writes into proper skills
    # (the 钱学森 "skill 延迟梳理" doctrine). Service is operator-
    # disabled-able via ``daily_review_enabled`` and exposes a
    # /api/review/run REST endpoint for manual fire.
    from .skills.daily_review_service import DailyReviewService
    from .skills.review_action_log import ReviewActionLog
    # Phase B+ — audit trail + rollback. Each successful review
    # run appends a record describing new/archived memory IDs and the
    # skill_history slice it produced; operator can rollback via REST
    # or IM later.
    review_action_log = ReviewActionLog(workspace_dir=settings.workspace_dir)
    daily_review_service = DailyReviewService(
        memory_store=memory_store,
        skill_history_store=skill_history_store,
        usage_store=usage_store,
        agent=agent,
        warmup_seconds=settings.daily_review_warmup_seconds,
        interval_seconds=settings.daily_review_interval_seconds,
        lookback_seconds=settings.daily_review_lookback_seconds,
        max_iterations=settings.daily_review_max_iterations,
        enabled=settings.daily_review_enabled,
        gateway_manager=manager,
        push_target_id=settings.daily_review_push_target_id,
        action_log=review_action_log,
    )

    memory_maintenance = MemoryMaintenance(memory_store, memory_manager, skill_curator)
    graph_scheduler = KnowledgeGraphScheduler(
        source_fn=collect_graph_records,
        export_dir=settings.workspace_dir / "graph",
        interval_seconds=settings.skill_knowledge_interval_seconds,
    )
    nightly_graph_pipeline = NightlyGraphPipeline(
        scheduler=graph_scheduler,
        export_dir=settings.workspace_dir / "graph",
        dispatch_fn=manager.dispatch,
        notify_target_id=settings.daily_review_push_target_id,
    )

    app.state.gateway_manager = manager
    app.state.cron_scheduler = cron_scheduler
    app.state.graph_scheduler = graph_scheduler
    app.state.nightly_graph_pipeline = nightly_graph_pipeline
    app.state.cron_runner = run_cron_job
    app.state.cron_deliverer = cron_deliverer
    app.state.agent = agent
    app.state.llm = llm_client
    app.state.graph_extractor = graph_extractor
    app.state.tool_registry = tool_registry
    app.state.confirmation_store = confirmation_store
    app.state.skill_loader = skill_loader
    app.state.usage_store = usage_store
    app.state.skill_curator = skill_curator
    app.state.curator_service = curator_service
    app.state.skill_knowledge_consolidator = skill_knowledge_consolidator
    app.state.skill_knowledge_service = skill_knowledge_service
    app.state.daily_review_service = daily_review_service
    app.state.memory_maintenance = memory_maintenance
    # Phase B+ — let the harness (and therefore the IM /review
    # command dispatch) see the service. Harness facade was built much
    # earlier so we patch the attribute in now, same pattern as
    # plugin_loader / tool_memo / tracer / progress below.
    app.state.harness.daily_review_service = daily_review_service
    app.state.memory_store = memory_store
    app.state.memory_manager = memory_manager
    app.state.wiki_store = wiki_store
    app.state.geo_store = geo_store  # v0.39.1
    app.state.failure_learner = agent._failure_learner
    app.state.mcp_manager = mcp_manager
    app.state.mcp_store = mcp_store
    app.state.mcp_lifecycle = mcp_lifecycle
    app.state.skill_history_store = skill_history_store

    # load local-directory plugins. Disabled by default; flip
    # ``plugins_enabled=True`` in env / Settings to opt in. Failures
    # in any single plugin never block boot.
    plugin_loader = None
    if settings.plugins_enabled:
        from .plugins.loader import PluginLoader
        plugin_loader = PluginLoader(
            plugins_dir=settings.plugins_dir or (settings.workspace_dir / "plugins"),
            tool_registry=tool_registry,
            workspace_dir=settings.workspace_dir,
            settings=settings,
            skill_guard=skill_guard,
            memory_manager=memory_manager,
        )
        try:
            plugin_loader.load_all()
        except Exception as exc:  # noqa: BLE001 — never block boot
            logger.exception("[plugins] loader crashed: {}", exc)
    app.state.plugin_loader = plugin_loader

    # Patch the live plugin_loader onto the harness now that boot
    # finished. The harness facade was created earlier so route_message
    # could close over it; this just plugs the late dependency in.
    app.state.harness.plugin_loader = plugin_loader

    # Phase 5 — acceleration + observability. Memo wraps the registry
    # first so the tracer captures the *post-cache* execute path; if a
    # call hits the memo, the tracer still records it (but with the
    # cached result, which keeps elapsed_ms ~0 — visible signal).
    tool_memo = ToolMemo()
    attach_tool_memo(tool_registry, tool_memo)
    app.state.harness.tool_memo = tool_memo
    tracer = TraceRecorder()
    attach_tracer(tracer=tracer, agent=agent, registry=tool_registry)
    app.state.harness.tracer = tracer
    # Phase 6 — progress emitter sits OUTSIDE tracer + memo, so each
    # registry.execute call visits progress → tracer → memo → real.
    # _run_agent_and_dispatch binds a per-turn sink that writes to the
    # original IM gateway, giving the user "🔎 搜索中…" style pings
    # while the agent does the heavy lifting.
    progress = ProgressEmitter()
    attach_progress(emitter=progress, registry=tool_registry)
    app.state.harness.progress = progress

    _harness_boot_inventory = app.state.harness.inventory()
    logger.info(
        "harness ready: skills={} mcps_runtime={} plugins={} tools_user={}"
        " (core_hidden: skills={} mcps_yaml={} tools={})",
        len(_harness_boot_inventory.skills),
        len(_harness_boot_inventory.mcps),
        len(_harness_boot_inventory.plugins),
        len(_harness_boot_inventory.tools),
        _harness_boot_inventory.core_summary.get("skills_hidden", 0),
        _harness_boot_inventory.core_summary.get("mcps_hidden", 0),
        _harness_boot_inventory.core_summary.get("tools_hidden", 0),
    )

    await manager.start()
    await cron_scheduler.start()
    await graph_scheduler.start()
    await curator_service.start()
    await skill_knowledge_service.start()
    await daily_review_service.start()

    try:
        yield
    finally:
        logger.info("shutting down {} v{}", settings.app_name, __version__)
        for task in tuple(ack_background_tasks):
            task.cancel()
        if ack_background_tasks:
            await asyncio.gather(*ack_background_tasks, return_exceptions=True)
        await daily_review_service.stop()
        await skill_knowledge_service.stop()
        await curator_service.stop()
        await graph_scheduler.stop()
        await cron_scheduler.stop()
        await manager.stop()
        if mcp_manager is not None:
            try:
                await mcp_manager.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[mcp] shutdown error: {}", exc)
        try:
            memory_manager.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[memory] shutdown error: {}", exc)
        # close the Redis pool and unwire the bundle cache so
        # tests / a soft-restart cleanly drop the shared client.
        try:
            _set_bundle_redis(None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[redis] unwire bundle cache failed: {}", exc)
        if redis_backend is not None:
            try:
                redis_backend.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[redis] close failed: {}", exc)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    # Permissive CORS for single-user / local-only deployments where the API
    # is reached from arbitrary local clients. v0.27 will tighten this with
    # an allowlist.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_api.root_router)
    app.include_router(health_api.router)
    app.include_router(runtime_api.router)
    app.include_router(graph_rag_api.router)
    app.include_router(skills_api.router)
    app.include_router(delivery_targets_api.router)
    app.include_router(cron_api.router)
    app.include_router(llm_api.router)
    app.include_router(tools_api.router)
    app.include_router(confirmations_api.router)
    app.include_router(curator_api.router)
    app.include_router(memory_api.router)
    app.include_router(knowledge_bases_api.router)
    app.include_router(maintenance_api.router)
    app.include_router(nightly_api.router)
    app.include_router(review_api.router)
    app.include_router(mcp_api.router)
    app.include_router(sessions_api.router)
    app.include_router(users_api.router)
    app.include_router(wiki_api.router)
    # gateway control plane + doctor. Mount AFTER weixin_api so the
    # legacy /api/gateways/weixin/{status,reload} routes still match first.
    app.include_router(weixin_api.router)
    app.include_router(gateways_api.build_router())
    app.include_router(doctor_api.build_router())
    app.include_router(plugins_api.build_router())
    app.include_router(harness_api.router)
    app.include_router(harness_obs_api.router)
    return app


app = create_app()
