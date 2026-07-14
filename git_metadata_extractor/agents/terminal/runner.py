"""Per-repo runner for the v2 terminal-agent PoC.

Flow for one source URL:

1.  Materialise a tempdir under `<output_dir>/<run_id>/`.
2.  Drop in:
      - `gimie.jsonld`  (GIMIE-extracted JSON-LD context)
      - `repo/`         (shallow git clone)
      - `MISSION.md`    (system prompt + JSON Schema target)
3.  Invoke the terminal agent headlessly with the configured skills loaded
    and a hard wallclock cap.
4.  Read `output.jsonld` from the tempdir, run strict-schema validation,
    and return a structured result.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from git_metadata_extractor.agents.models import generate_uuid
from git_metadata_extractor.agents.terminal.pi_config import PiProviderSpec, write_pi_config
from git_metadata_extractor.agents.terminal.schema_cheatsheet import build_cheatsheet, get_shape_constraints

logger = logging.getLogger(__name__)

# Built-in tools the executor is allowed to call. `bash` stays in the
# allowlist for repo navigation (`ls`, `find`, `grep`, `cat`) — the
# install/host-mod regex denylist in `pi_extension/index.ts` is the
# defense-in-depth layer that keeps it tame.
_PI_BUILTIN_TOOL_ALLOWLIST = ("bash", "read", "write")


# Map the JSON-LD `@type` values our agent emits to the entity keys
# `StrictSchemaValidator` understands (`person`, `organization`, …).
# The schema_validation module uses lowercase singulars; agents emit
# CURIEs (`schema:Person`, `org:Organization`, etc.). The repository
# class has the awkward asymmetry — `schema:SoftwareSourceCode` maps to
# `repository` rather than `softwaresourcecode`.
_JSONLD_TYPE_TO_ENTITY_KEY = {
    "schema:SoftwareSourceCode": "repository",
    "schema:Person": "person",
    "org:Organization": "organization",
    "schema:ScholarlyArticle": "article",
    "org:Membership": "membership",
    "pulse:Contribution": "contribution",
}


@dataclass(slots=True)
class TerminalRunCaps:
    max_tool_calls: int
    max_wallclock_s: int
    max_context_bytes: int
    # Maximum executor attempts per source URL inside a repair loop
    # (each attempt is a fresh pi invocation). 1 = no repair, single shot.
    max_iterations: int = 1


@dataclass(slots=True)
class TerminalRunResult:
    run_id: str
    source_url: str
    workdir: Path
    output_jsonld: dict[str, Any] | None
    transcript_path: Path | None
    elapsed_s: float
    tool_calls: int
    wallclock_exceeded: bool
    schema_valid: bool
    schema_errors: list[str] = field(default_factory=list)
    error: str | None = None
    # Token usage parsed from pi's `--mode json` `message` events
    # (assistant messages carry `usage.input/output/cacheRead/cacheWrite`).
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    iteration: int = 1


@dataclass(slots=True)
class TerminalRunner:
    output_dir: Path
    executor_model: str
    executor_base_url: str
    executor_api_key_env: str
    skills: list[str]
    caps: TerminalRunCaps
    harness_kind: str
    harness_binary: str
    harness_extra_args: list[str]
    # Extra pi extensions to load via `-e <path>`. Defaults to None so
    # the runner falls back to just the in-repo bash-blacklist
    # extension. The terminal_subagent module overrides this to ALSO
    # load pi-mono's subagent tool.
    extra_extensions: list[Path] | None = None
    # Mission template the runner renders into `<workdir>/MISSION_v<N>.md`.
    # Defaults to the simple incremental mission. The terminal_subagent
    # module points this at its orchestrator template so the LLM
    # delegates via the `subagent` tool instead of doing everything itself.
    mission_template_path: Path | None = None
    # Tool names to add to the `--tools` allowlist alongside the builtin
    # `bash,read,write` set. Pi's allowlist applies to extension tools
    # too, so a runtime that loads an extension registering custom tools
    # (e.g. `subagent`) MUST list them here or the LLM will see a
    # "Tool not found" error. Empty by default.
    extra_allowed_tools: list[str] | None = None

    def run(self, source_url: str) -> TerminalRunResult:
        """Single-attempt run. For multi-attempt repair use `run_with_repair`."""
        run_id, workdir = self._make_workdir()
        logger.info("terminal-run %s starting (url=%s, workdir=%s)", run_id, source_url, workdir)
        try:
            self._bootstrap_pi_config(workdir)
            self._materialise_context(source_url, workdir)
            return self._run_iteration(
                run_id=run_id,
                workdir=workdir,
                source_url=source_url,
                iteration=1,
                prior_judge=None,
            )
        except Exception as err:  # noqa: BLE001 — runner is the boundary
            logger.exception("terminal-run %s setup failed", run_id)
            return TerminalRunResult(
                run_id=run_id,
                source_url=source_url,
                workdir=workdir,
                output_jsonld=None,
                transcript_path=None,
                elapsed_s=0.0,
                tool_calls=0,
                wallclock_exceeded=False,
                schema_valid=False,
                error=str(err),
            )

    def run_with_repair(
        self,
        source_url: str,
        judge: Any,
        *,
        max_iterations: int | None = None,
    ) -> RepairLoopResult:
        """Execute up to `max_iterations` attempts, feeding judge feedback back.

        Each iteration after the first reuses the workdir + cloned repo +
        gimie, and renders a fresh `MISSION_v<N>.md` that includes the
        prior-attempt's output and the judge's missing/hallucinated lists
        as a dedicated FEEDBACK section. The loop exits early when the
        judge votes pass.
        """
        cap = max_iterations or self.caps.max_iterations
        run_id, workdir = self._make_workdir()
        logger.info(
            "terminal-repair %s starting (url=%s, workdir=%s, max_iter=%d)",
            run_id, source_url, workdir, cap,
        )
        attempts: list[tuple[TerminalRunResult, Any]] = []
        loop_start = time.monotonic()

        try:
            self._bootstrap_pi_config(workdir)
            self._materialise_context(source_url, workdir)
        except Exception as err:  # noqa: BLE001 — surface setup failure
            logger.exception("terminal-repair %s setup failed", run_id)
            placeholder = TerminalRunResult(
                run_id=run_id, source_url=source_url, workdir=workdir,
                output_jsonld=None, transcript_path=None, elapsed_s=0.0,
                tool_calls=0, wallclock_exceeded=False, schema_valid=False,
                error=str(err),
            )
            return RepairLoopResult(
                run_id=run_id, source_url=source_url, workdir=workdir,
                attempts=[(placeholder, None)], total_elapsed_s=time.monotonic() - loop_start,
            )

        gimie_payload = _safe_load_gimie(workdir)
        prior_judge: Any = None
        for iteration in range(1, cap + 1):
            run_result = self._run_iteration(
                run_id=run_id, workdir=workdir, source_url=source_url,
                iteration=iteration, prior_judge=prior_judge,
            )
            judge_report: Any = None
            if run_result.output_jsonld is not None:
                try:
                    judge_report = judge.judge(
                        output_jsonld=run_result.output_jsonld,
                        transcript_path=run_result.transcript_path,
                        gimie_jsonld=gimie_payload,
                        shacl_conforms=run_result.schema_valid,
                        shacl_errors=run_result.schema_errors,
                    )
                except Exception as err:  # noqa: BLE001 — judge failure is non-fatal
                    logger.warning("terminal-repair %s judge failed at iter %d: %s", run_id, iteration, err)
            attempts.append((run_result, judge_report))
            if judge_report is not None and judge_report.verdict == "pass":
                logger.info("terminal-repair %s converged at iter %d", run_id, iteration)
                break
            if run_result.output_jsonld is None or run_result.error:
                logger.warning("terminal-repair %s aborting at iter %d (no output / executor error)", run_id, iteration)
                break
            prior_judge = judge_report
        return RepairLoopResult(
            run_id=run_id, source_url=source_url, workdir=workdir,
            attempts=attempts, total_elapsed_s=time.monotonic() - loop_start,
        )

    def _make_workdir(self) -> tuple[str, Path]:
        run_id = uuid.uuid4().hex[:12]
        # Resolve to absolute. Pi runs with `cwd=workdir`, and several
        # of its inputs (PI_CODING_AGENT_DIR, `@<path>` for the mission)
        # are interpreted relative to the process's cwd. A relative
        # workdir would get re-resolved against itself and double the
        # path. Resolving here propagates absolute paths everywhere.
        workdir = (self.output_dir / run_id).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        return run_id, workdir

    def _run_iteration(
        self,
        *,
        run_id: str,
        workdir: Path,
        source_url: str,
        iteration: int,
        prior_judge: Any,
    ) -> TerminalRunResult:
        """One executor invocation: render mission, run pi, validate output.

        On iteration 2+, `prior_judge` carries the previous attempt's
        verdict; the rendered MISSION_v<N>.md includes its missing /
        hallucinated lists and a snapshot of the prior `output.jsonld`.
        """
        start = time.monotonic()
        wallclock_exceeded = False
        # Roll the previous output.jsonld out of the way so iteration N
        # can identify cleanly whether the agent produced a new one.
        if iteration > 1:
            prev_out = workdir / "output.jsonld"
            if prev_out.exists():
                prev_out.rename(workdir / f"output_v{iteration - 1}.jsonld")

        mission_path = workdir / f"MISSION_v{iteration}.md"
        self._write_mission(
            source_url=source_url, workdir=workdir, mission_path=mission_path,
            iteration=iteration, prior_judge=prior_judge,
        )
        # Iteration 1: pre-seed `output.jsonld` with @context + empty
        # @graph so the agent's loop is "read → append → write" from
        # the very first turn (no "what shape do I need?" detour). On
        # iteration 2+, the prior attempt's output is the seed.
        if iteration == 1:
            self._write_output_skeleton(workdir, source_url)

        transcript_path: Path | None = workdir / f"transcript_v{iteration}.jsonl"
        try:
            transcript_path = self._invoke_agent(
                workdir=workdir, mission_path=mission_path, transcript_path=transcript_path,
            )
        except subprocess.TimeoutExpired:
            wallclock_exceeded = True
            logger.warning(
                "terminal-run %s iter %d exceeded wallclock cap (%ds)",
                run_id, iteration, self.caps.max_wallclock_s,
            )

        stats = _extract_run_stats(transcript_path)
        try:
            output_payload = self._load_output(workdir)
        except FileNotFoundError:
            output_payload = None
        schema_valid, errors = self._validate(output_payload)
        return TerminalRunResult(
            run_id=run_id,
            source_url=source_url,
            workdir=workdir,
            output_jsonld=output_payload,
            transcript_path=transcript_path,
            elapsed_s=time.monotonic() - start,
            tool_calls=stats.tool_calls,
            wallclock_exceeded=wallclock_exceeded,
            schema_valid=schema_valid,
            schema_errors=errors,
            iteration=iteration,
            tokens_input=stats.tokens_input,
            tokens_output=stats.tokens_output,
            tokens_cache_read=stats.tokens_cache_read,
            tokens_cache_write=stats.tokens_cache_write,
        )

    def _bootstrap_pi_config(self, workdir: Path) -> None:
        """Write a fresh `models.json` + `settings.json` for this run.

        Called at the start of every `run()` — pi reads these from
        `PI_CODING_AGENT_DIR`, which the runner sets to
        `<workdir>/pi-agent` when invoking the harness. No global pi
        config is touched and the actual API token never lands on disk
        (pi resolves `apiKey: "RCP_TOKEN"` as an env var lookup).
        """
        if self.harness_kind != "pi":
            return  # other harnesses bring their own configuration model
        agent_dir = workdir / "pi-agent"
        provider = PiProviderSpec(
            name="EPFL RCP",
            base_url=self.executor_base_url,
            api_key_env=self.executor_api_key_env,
            models=[self.executor_model],
        )
        write_pi_config(agent_dir, provider=provider, default_model=self.executor_model)

    def _pi_env(self, workdir: Path) -> dict[str, str]:
        """Build the env dict for the pi subprocess.

        Inherits the parent env (so `RCP_TOKEN` and friends pass through),
        overlays:
          - `PI_CODING_AGENT_DIR` — points pi at the run-local config
          - `PI_OFFLINE=1`        — no update checks, no telemetry pings
          - `PYTHONPATH`          — prepend the project root so the agent
                                    can `python -m git_metadata_extractor.skills.<X>`
                                    even when console scripts aren't
                                    installed yet.
        """
        env = dict(os.environ)
        env["PI_CODING_AGENT_DIR"] = str(workdir / "pi-agent")
        env["PI_OFFLINE"] = "1"
        project_root = str(_project_root())
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{project_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else project_root
        )
        return env

    def _materialise_context(self, source_url: str, workdir: Path) -> None:
        """Write `gimie.jsonld`, `schema_cheatsheet.md`, and clone the repo."""
        # Cheatsheet lands in the workdir alongside gimie.jsonld so the
        # agent can read it BEFORE emitting any entity. It enumerates
        # the closed-shape allowed/required properties per @type — without
        # this, the agent emits valid schema.org properties that the v2
        # SHACL shapes reject (legalName, logo, description, affiliation).
        try:
            (workdir / "schema_cheatsheet.md").write_text(build_cheatsheet(), encoding="utf-8")
        except Exception as err:  # noqa: BLE001 — soft failure
            logger.warning("failed to write schema_cheatsheet.md: %s", err)
        # gimie: reuse the v1 helper because it carries monkey-patches
        # against a known empty-repo crash and a REST-query bug; importing
        # plain `gimie.project.Project` here would re-introduce those.
        # The helper also normalises the legacy `GITHUB_TOKEN` env that
        # gimie reads (gimie 401s on a comma-separated pool), so the
        # call site doesn't need to wrap it any further.
        from git_metadata_extractor.providers.gimie_extract import extract_gimie  # noqa: PLC0415

        try:
            gimie_payload = extract_gimie(source_url, serialization_format="json-ld")
        except Exception as err:  # noqa: BLE001 — gimie is best-effort
            logger.warning("gimie extraction failed for %s: %s", source_url, err)
            gimie_payload = {"@graph": [], "_gimie_error": str(err)}
        (workdir / "gimie.jsonld").write_text(
            json.dumps(gimie_payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        # Shallow clone — single commit, no history. Cap clone time at
        # the wallclock budget so a hung clone can't consume the whole run.
        repo_dir = workdir / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", source_url, str(repo_dir)],
                check=True,
                capture_output=True,
                timeout=min(120, self.caps.max_wallclock_s),
            )
        except subprocess.CalledProcessError as err:
            stderr = err.stderr.decode("utf-8", errors="replace") if err.stderr else ""
            logger.warning("git clone failed for %s: %s", source_url, stderr.strip())
            (workdir / "_clone_error.txt").write_text(stderr, encoding="utf-8")
        except subprocess.TimeoutExpired:
            logger.warning("git clone timed out for %s", source_url)
            (workdir / "_clone_error.txt").write_text("timeout", encoding="utf-8")

    def _write_mission(
        self,
        *,
        source_url: str,
        workdir: Path,
        mission_path: Path | None = None,
        iteration: int = 1,
        prior_judge: Any = None,
    ) -> None:
        """Render the MISSION template, optionally with a feedback section.

        On iteration ≥ 2, appends a "Prior attempt rejected" section that
        embeds the prior `output.jsonld` + judge's missing/hallucinated
        lists so the agent can do targeted edits instead of restarting.
        """
        template_path = self.mission_template_path or (Path(__file__).parent / "prompts" / "mission.md")
        template = template_path.read_text(encoding="utf-8")
        skills_block = "\n".join(
            f"- **{name}** — see `skills/{name}/SKILL.md` for usage."
            for name in self.skills
        )
        rendered = (
            template
            .replace("{{ source_url }}", source_url)
            .replace("{{ skills_block }}", skills_block)
            .replace("{{ max_tool_calls }}", str(self.caps.max_tool_calls))
        )
        if iteration > 1 and prior_judge is not None:
            rendered += "\n\n" + _render_repair_section(workdir, iteration, prior_judge)

        target = mission_path or (workdir / "MISSION.md")
        target.write_text(rendered, encoding="utf-8")

    def _invoke_agent(
        self,
        *,
        workdir: Path,
        mission_path: Path | None = None,
        transcript_path: Path | None = None,
    ) -> Path:
        """Spawn pi headlessly, capture transcript + stderr.

        Returns the path to the transcript JSONL (one event per line, in
        pi's `--mode json` schema). Raises on wallclock timeout — the
        caller stamps `wallclock_exceeded`.
        """
        project_root = _project_root()
        # Always load the bash-blacklist extension. Append any extras
        # the runtime configured (e.g. terminal_subagent loads pi-mono's
        # subagent extension on top so the orchestrator can fan out).
        bash_ext = project_root / "git_metadata_extractor" / "agents" / "terminal" / "pi_extension" / "index.ts"
        ext_paths: list[Path] = [bash_ext, *(self.extra_extensions or [])]
        skill_args: list[str] = []
        for skill_name in self.skills:
            skill_dir = project_root / "git_metadata_extractor" / "skills" / skill_name
            if (skill_dir / "SKILL.md").exists():
                skill_args.extend(["--skill", str(skill_dir)])

        mission_file = mission_path or (workdir / "MISSION.md")
        # Pi argv shape:
        #   `-p`           is a boolean flag (--print mode).
        #   `@<path>`      loads file content as the initial user message
        #                  (positional `[@files...]` syntax in pi help).
        #   `--provider` / `--model` are mandatory for us — pi's
        #                  settings.json `defaultProvider`/`defaultModel`
        #                  are ignored at runtime, only honoured by
        #                  `pi --list-models`. Without these, pi falls
        #                  back to its built-in default (`github-copilot`)
        #                  and 401s.
        ext_args: list[str] = []
        for path in ext_paths:
            ext_args.extend(["-e", str(path)])
        argv: list[str] = [
            self.harness_binary,
            "-p",
            "--provider",
            "rcp",
            "--model",
            self.executor_model,
            *ext_args,
            "--mode",
            "json",
            "--no-session",
            "--no-extensions",
            "--tools",
            ",".join([*_PI_BUILTIN_TOOL_ALLOWLIST, *(self.extra_allowed_tools or [])]),
            *skill_args,
            *self.harness_extra_args,
            f"@{mission_file}",
        ]

        out_transcript = transcript_path or (workdir / "transcript.jsonl")
        stderr_path = out_transcript.with_suffix(".stderr.log")
        logger.info(
            "spawning pi (cwd=%s, mission=%s, timeout=%ds, skills=%d)",
            workdir, mission_file.name, self.caps.max_wallclock_s, len(skill_args) // 2,
        )
        try:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                env=self._pi_env(workdir),
                capture_output=True,
                timeout=self.caps.max_wallclock_s,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            # Surface what pi managed to emit before the cap. The caller
            # catches this and stamps wallclock_exceeded.
            out_transcript.write_bytes(err.stdout or b"")
            stderr_path.write_bytes(err.stderr or b"")
            raise

        out_transcript.write_bytes(completed.stdout)
        stderr_path.write_bytes(completed.stderr)
        if completed.returncode != 0:
            logger.warning("pi exited with code %d (see %s)", completed.returncode, stderr_path)
        return out_transcript

    def _load_output(self, workdir: Path) -> dict[str, Any]:
        """Read `<workdir>/output.jsonld`, scrub disallowed properties, finalise UUIDs."""
        path = workdir / "output.jsonld"
        if not path.exists():
            msg = f"agent produced no output.jsonld at {path}"
            raise FileNotFoundError(msg)
        payload = json.loads(path.read_text(encoding="utf-8"))

        # Deterministic safety net BEFORE the SHACL gate: drop properties
        # not in the closed shape for each entity's @type, and fix the
        # known date-only → xsd:dateTime hotfix. Even if the agent ignored
        # both schema_cheatsheet.md and the validate-output skill, the
        # scrubber guarantees no structural-mistake reaches the judge.
        scrub_stats = _scrub_to_shape(payload)
        if any(scrub_stats.values()):
            logger.info(
                "scrubbed payload: dropped=%d properties, fixed=%d datetimes",
                scrub_stats["dropped_properties"],
                scrub_stats["fixed_datetimes"],
            )

        replaced = _finalise_placeholder_uuids(payload)
        if replaced > 0:
            logger.info("replaced %d placeholder UUID(s) with real UUIDv4s", replaced)

        # Persist the post-processed payload so envelope.json, the SHACL
        # gate, and the judge all see the same canonical document.
        if replaced > 0 or any(scrub_stats.values()):
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return payload

    def _write_output_skeleton(self, workdir: Path, source_url: str) -> None:
        """Pre-seed `output.jsonld` with @context + empty @graph.

        Loads the canonical v2 JSON-LD context inline so the agent never
        has to hunt for it. Adds a tiny `_meta` block (stripped on
        validation per v2's `_*` convention) carrying the source URL
        for the agent's reference — no semantic meaning.
        """
        skeleton: dict[str, Any] = {
            "@context": _load_jsonld_context(),
            "@graph": [],
            "_meta": {"source_url": source_url, "build_mode": "incremental"},
        }
        (workdir / "output.jsonld").write_text(
            json.dumps(skeleton, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _validate(self, payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
        """Run SHACL validation against the v2 ontology shapes graph.

        We use SHACL (not the strict JSON Schema validator) because the
        terminal agent emits final-form JSON-LD with `@id`/`@type`
        CURIEs; the strict schemas are designed for the v2 pipeline's
        intermediate agent-payload form (`id`/`type`/`idSource`) and
        would reject every entity for shape mismatch.

        Returns `(conforms, errors)`. `errors` is a list of human-readable
        violation strings; the same list is forwarded to the judge so it
        can factor structural issues into its verdict.
        """
        if payload is None:
            return False, ["no payload"]
        graph = payload.get("@graph")
        if not isinstance(graph, list) or not graph:
            return False, ["@graph missing or empty"]
        try:
            from rdflib import Graph as RDFGraph  # noqa: PLC0415

            from git_metadata_extractor.validation import (  # noqa: PLC0415
                SHACLRuntimeUnavailableError,
                SHACLValidator,
                load_ontology_shapes_graph,
            )
        except ImportError as err:
            return True, [f"shacl deps missing: {err}"]

        # Strip private `_*` keys before serialising — they're internal
        # pipeline metadata (e.g. `_meta`) and would otherwise add bogus
        # nodes that can confuse SHACL.
        scrubbed = _strip_internal_metadata(payload)
        try:
            data_graph = RDFGraph()
            data_graph.parse(data=json.dumps(scrubbed), format="json-ld")
        except Exception as err:  # noqa: BLE001 — pre-SHACL parse stage
            return False, [f"jsonld parse failed: {err}"]
        try:
            result = SHACLValidator().validate_graph(data_graph, load_ontology_shapes_graph())
        except SHACLRuntimeUnavailableError as err:
            # Treat as soft-pass: judge still gets to decide. Better than
            # forcing a fail when the deployment lacks pyshacl.
            return True, [f"shacl runtime unavailable: {err}"]
        except Exception as err:  # noqa: BLE001 — SHACL pipeline may break in many ways
            return False, [f"shacl validate failed: {err}"]

        errors = [
            (
                f"violation: focus={v.get('focusNode')}, "
                f"path={v.get('path')}, "
                f"message={v.get('message')}"
            )
            for v in result.violations
        ]
        # Surface warnings too — they're often pre-violation signals
        # (e.g. unknown ontology terms) that the judge should see.
        for warn in result.warnings:
            errors.append(
                f"warning: focus={warn.get('focusNode')}, "
                f"path={warn.get('path')}, "
                f"message={warn.get('message')}"
            )
        return result.conforms, errors


@dataclass(slots=True)
class RepairLoopResult:
    """Aggregate over one or more executor attempts plus their judgements."""

    run_id: str
    source_url: str
    workdir: Path
    # Each tuple: (attempt result, judge report or None if judge unavailable
    # / executor produced no output). Length ≥ 1.
    attempts: list[tuple[TerminalRunResult, Any]]
    total_elapsed_s: float

    @property
    def final_attempt(self) -> TerminalRunResult:
        return self.attempts[-1][0]

    @property
    def final_judge(self) -> Any:
        return self.attempts[-1][1]

    @property
    def final_verdict(self) -> str:
        for _, judge_report in reversed(self.attempts):
            if judge_report is not None:
                return judge_report.verdict
        return "no-judge"

    @property
    def best_attempt(self) -> tuple[TerminalRunResult, Any]:
        """Pick the iteration with the strongest combined signal.

        Empirical finding from the 2026-05-04 round-2 ablation: repair
        iter 2 routinely outperforms iter 1 (judge feedback works) but
        iter 3 regresses (the agent over-edits chasing the judge's
        latest list). Returning the LAST attempt therefore loses
        consistently to returning the BEST attempt by judge score.

        Selection key (lexicographic, lower is better):
          1. SHACL violations (`schema_valid=False` is worse).
          2. missing + hallucinated count from the judge.
          3. iteration index (prefer earlier on ties — cheaper).

        Falls back to the last attempt if no judge report is available.
        """
        scored: list[tuple[tuple[int, int, int], TerminalRunResult, Any]] = []
        for run_result, judge_report in self.attempts:
            shacl_pen = 0 if run_result.schema_valid else 1
            judge_pen = (
                len(getattr(judge_report, "missing", []) or [])
                + len(getattr(judge_report, "hallucinated", []) or [])
                if judge_report is not None
                else 1_000_000  # missing judge → worst possible score
            )
            scored.append(((shacl_pen, judge_pen, run_result.iteration), run_result, judge_report))
        scored.sort(key=lambda x: x[0])
        if not scored:
            return self.attempts[-1]
        _, best_run, best_judge = scored[0]
        return best_run, best_judge

    @property
    def total_executor_tokens_input(self) -> int:
        return sum(a.tokens_input for a, _ in self.attempts)

    @property
    def total_executor_tokens_output(self) -> int:
        return sum(a.tokens_output for a, _ in self.attempts)

    @property
    def total_judge_tokens_prompt(self) -> int:
        return sum(j.tokens_prompt for _, j in self.attempts if j is not None)

    @property
    def total_judge_tokens_completion(self) -> int:
        return sum(j.tokens_completion for _, j in self.attempts if j is not None)


# Match a date-only ISO 8601 string (`2022-12-07`) — i.e. an `xsd:date`
# value where an `xsd:dateTime` is required. The fix is to append
# `T00:00:00Z` and let SHACL parse it as a real datetime.
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scrub_to_shape(payload: dict[str, Any]) -> dict[str, int]:
    """Drop disallowed properties + fix date-only→dateTime in `@graph`.

    Mutates `payload` in place. Returns a stats dict the runner logs at
    INFO level. The function is the deterministic counterpart to the
    `validate-output` skill: anything the agent missed gets cleaned up
    before SHACL runs.

    Soft-fails (returns zero stats, leaves payload alone) if the shape
    constraints are unavailable — better to skip cleaning than to
    silently break a partial output.
    """
    stats = {"dropped_properties": 0, "fixed_datetimes": 0}
    graph = payload.get("@graph")
    if not isinstance(graph, list):
        return stats
    try:
        constraints = get_shape_constraints()
    except Exception as err:  # noqa: BLE001 — soft failure
        logger.warning("scrub: get_shape_constraints failed (%s); skipping", err)
        return stats
    if not constraints:
        return stats

    # Properties pi reads but the v2 ontology doesn't define are kept
    # untouched: `@id`, `@type`, `identifiers` (UUID slot), and any
    # JSON-LD aliases pulled in via `@context`. Without this, scrubbing
    # would remove the agent's identifier hooks.
    structural_keep = {"@id", "@type", "@context", "identifiers"}

    for entity in graph:
        if not isinstance(entity, dict):
            continue
        raw_type = entity.get("@type")
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else None
        if not isinstance(raw_type, str):
            continue
        shape = constraints.get(raw_type)
        if shape is None:
            continue  # unknown @type — leave it for SHACL to flag
        allowed = shape["allowed"]
        datetime_props = shape["datetime_props"]
        for key in list(entity.keys()):
            if key in structural_keep:
                continue
            if key not in allowed:
                del entity[key]
                stats["dropped_properties"] += 1
                continue
            if key in datetime_props:
                entity[key] = _coerce_datetime(entity[key], stats)
    return stats


def _coerce_datetime(value: Any, stats: dict[str, int]) -> Any:
    """Normalise an `xsd:dateTime` value before SHACL.

    Two common agent mistakes get corrected:
    1. Date-only string (`2022-12-07`) — append `T00:00:00Z` so it
       parses as `xsd:dateTime`.
    2. `{"@value": "..."}` wrapper without `@type` — unwrap to plain
       string. The v2 `@context` already declares the field's type;
       wrapping it without `@type` makes rdflib treat the value as a
       plain string literal and SHACL fails the datatype constraint.

    Lists are coerced element-wise.
    """
    if isinstance(value, list):
        return [_coerce_datetime(v, stats) for v in value]
    # Unwrap JSON-LD value form when no @type is declared — the
    # @context already binds the type, so plain string is what SHACL
    # needs to see for datatype matching to succeed.
    if isinstance(value, dict) and "@value" in value and "@type" not in value:
        value = value["@value"]
        stats["fixed_datetimes"] += 1
    if isinstance(value, str) and _DATE_ONLY_RE.match(value):
        stats["fixed_datetimes"] += 1
        return f"{value}T00:00:00Z"
    return value


def _strip_internal_metadata(payload: Any) -> Any:
    """Return a deep copy with all `_*`-prefixed keys removed.

    The v2 convention treats `_*` fields as internal pipeline metadata
    that must not reach external artefacts (JSON-LD, RDF, SHACL).
    """
    if isinstance(payload, dict):
        return {
            k: _strip_internal_metadata(v)
            for k, v in payload.items()
            if not (isinstance(k, str) and k.startswith("_"))
        }
    if isinstance(payload, list):
        return [_strip_internal_metadata(v) for v in payload]
    return payload


def _safe_load_gimie(workdir: Path) -> dict[str, Any] | None:
    path = workdir / "gimie.jsonld"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _render_repair_section(workdir: Path, iteration: int, prior_judge: Any) -> str:
    """Render the feedback block appended to MISSION_v<N>.md when iter ≥ 2."""
    prior_output_path = workdir / f"output_v{iteration - 1}.jsonld"
    prior_output_blob = ""
    if prior_output_path.exists():
        try:
            prior = json.loads(prior_output_path.read_text(encoding="utf-8"))
            prior_output_blob = json.dumps(prior, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, OSError):
            prior_output_blob = prior_output_path.read_text(encoding="utf-8", errors="replace")

    missing = getattr(prior_judge, "missing", []) or []
    hallucinated = getattr(prior_judge, "hallucinated", []) or []
    rationale = getattr(prior_judge, "rationale", "") or ""

    lines = [
        "---",
        "",
        f"# REPAIR ATTEMPT (iteration {iteration})",
        "",
        "Your previous output was rejected by an independent reviewer. Read",
        "the feedback below CAREFULLY and produce a new `output.jsonld` that",
        "addresses every item. Do not regress on parts that were correct.",
        "",
        "## Reviewer rationale",
        "",
        rationale or "(no rationale provided)",
        "",
        "## Missing entities or fields (must be added)",
        "",
    ]
    if missing:
        lines.extend(f"- {item}" for item in missing)
    else:
        lines.append("(none)")
    lines += [
        "",
        "## Hallucinated / unsupported claims (must be removed or evidenced)",
        "",
    ]
    if hallucinated:
        lines.extend(f"- {item}" for item in hallucinated)
    else:
        lines.append("(none)")
    lines += [
        "",
        "## Your previous attempt (rejected)",
        "",
        "```json",
        prior_output_blob if prior_output_blob else "(no prior output captured)",
        "```",
        "",
        "Now produce a corrected `output.jsonld`. Every claim you keep must",
        "still be backed by a tool call you make in this attempt — the",
        "reviewer compares against THIS attempt's transcript, not earlier",
        "ones.",
    ]
    return "\n".join(lines)


def _project_root() -> Path:
    """Find the repo root by walking up to a `pyproject.toml`."""
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parents[3]


# Placeholder UUIDs the agent emits per the MISSION rule
# "use 00000000-0000-0000-0000-XXXXXXXXXXXX, the orchestrator replaces
# them post-run". Any string matching this exact zero-prefixed shape is
# treated as synthetic — real UUIDv4s never have 20 leading zeros.
_PLACEHOLDER_UUID_RE = re.compile(r"^0{8}-0{4}-0{4}-0{4}-[0-9a-fA-F]{12}$")


def _finalise_placeholder_uuids(payload: Any) -> int:
    """Walk a JSON payload in place; replace placeholder UUIDs with real ones.

    Returns the count of replacements. Non-string nodes are left alone;
    string nodes that don't match the placeholder regex are also left
    alone, so no risk of clobbering real ORCID/ROR/DOI ids that happen
    to live in the same payload.
    """
    counter = [0]

    def _walk(node: Any) -> Any:
        if isinstance(node, dict):
            for k, v in list(node.items()):
                node[k] = _walk(v)
            return node
        if isinstance(node, list):
            for i, v in enumerate(node):
                node[i] = _walk(v)
            return node
        if isinstance(node, str) and _PLACEHOLDER_UUID_RE.match(node):
            counter[0] += 1
            return generate_uuid()
        return node

    _walk(payload)
    return counter[0]


def _load_jsonld_context() -> Any:
    """Return the canonical v2 `@context` mapping (inlined, not a URI).

    The v2 ontology lives in-repo at
    `git_metadata_extractor/schema/json/context/v2.0.jsonld`; we inline the mapping into
    the seed `output.jsonld` so the agent has zero ambiguity about
    namespaces. Falls back to a string URL if the file is unreadable
    (which would be surprising and means the repo layout has shifted).
    """
    try:
        from git_metadata_extractor.schema import load_jsonld_context  # noqa: PLC0415

        return load_jsonld_context()
    except Exception as err:  # noqa: BLE001 — last-resort fallback
        logger.warning("could not load v2 @context (%s); falling back to URI", err)
        return "https://imaging-plaza.epfl.ch/contexts/pulse-v2.jsonld"


@dataclass(slots=True)
class _RunStats:
    tool_calls: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0


def _extract_run_stats(transcript_path: Path | None) -> _RunStats:
    """Parse pi's `--mode json` transcript for tool-call count + token usage.

    Pi emits one JSON object per line. Two kinds of events matter here:
      - `tool_execution_start` → increments the tool_calls counter.
      - assistant `message` (or `message_end`) events whose payload has
        `message.usage.{input,output,cacheRead,cacheWrite}` → those
        accumulate into the run's token totals.

    Per-message usage values are summed because pi emits one assistant
    message per turn and the agent may take many turns.
    """
    stats = _RunStats()
    if transcript_path is None or not transcript_path.exists():
        return stats
    with transcript_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()  # noqa: PLW2901
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "tool_execution_start":
                stats.tool_calls += 1
                continue
            # Token usage rides on assistant messages. Pi emits both
            # `message` (session-format) and `message_end` (json-mode
            # event) shapes — handle both.
            if event_type in {"message", "message_end"}:
                msg = event.get("message", {})
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        stats.tokens_input += int(usage.get("input", 0) or 0)
                        stats.tokens_output += int(usage.get("output", 0) or 0)
                        stats.tokens_cache_read += int(usage.get("cacheRead", 0) or 0)
                        stats.tokens_cache_write += int(usage.get("cacheWrite", 0) or 0)
    return stats


__all__ = ["RepairLoopResult", "TerminalRunCaps", "TerminalRunResult", "TerminalRunner"]
