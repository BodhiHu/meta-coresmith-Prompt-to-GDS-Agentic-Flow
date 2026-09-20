# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Deterministic post-route RC extraction and static-timing sign-off.

The physical backend used to ask an LLM to judge timing numbers copied from
the PnR worker.  This module instead invokes the active deployment's
``run_sta`` implementation on the routed DEF, using that deployment's own RCX
recipe, and derives the verdict solely from persisted tool reports.

This is deliberately deployment-neutral at the graph boundary.  A deployment
that cannot render an extracted-timing script fails with an actionable
capability error; the graph never substitutes a local PDK or guesses RC rules.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from orchestrator.pdk.base import ToolRequest

_SLACK_RE = re.compile(
    r"^\s*(\S+)\s+slack\s+\((?:MET|VIOLATED)\)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_WNS_RE = re.compile(
    r"^\s*wns(?:\s+\S+)?\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_TNS_RE = re.compile(
    r"^\s*tns(?:\s+\S+)?\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_AREA_RE = re.compile(
    r"Design\s+area\s+"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+um\^2"
    r"(?:\s+([+-]?(?:\d+(?:\.\d*)?|\.\d+))%\s+utilization)?",
    re.IGNORECASE,
)
_TOTAL_POWER_RE = re.compile(
    r"^\s*Total\s+(?:\S+\s+){3}"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?:\s+100\.0%)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_PARTIAL_UNANNOTATED_RE = re.compile(
    r"Found\s+(\d+)\s+partially\s+unannotated\s+drivers?",
    re.IGNORECASE,
)
_UNANNOTATED_BLOCK_RE = re.compile(
    r"Found\s+(\d+)\s+unannotated\s+drivers?\.?(.*?)"
    r"(?=Found\s+\d+\s+partially\s+unannotated\s+drivers?|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_REPORT_NAMES = (
    "setup.rpt",
    "hold.rpt",
    "sta.rpt",
    "constraint_checks.rpt",
    "parasitic_annotation.rpt",
    "clocks.rpt",
    "power.rpt",
    "area.rpt",
)
_REQUIRED_REPORT_NAMES = _REPORT_NAMES[:6]


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _last_float(pattern: re.Pattern[str], text: str) -> float | None:
    matches = pattern.findall(text)
    if not matches:
        return None
    try:
        value = float(matches[-1])
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _minimum_float(pattern: re.Pattern[str], text: str) -> float | None:
    """Return the worst of every reported value, rejecting any non-finite."""
    matches = pattern.findall(text)
    if not matches:
        return None
    try:
        values = [float(value) for value in matches]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return min(values)


def _instrument_rcx_tcl(template: str) -> str:
    """Add machine-auditable reports to a deployment-owned RCX recipe.

    The deployment remains the authority for libraries, corners, RC rules and
    extraction commands.  The adapter only persists reports needed to make a
    fail-closed verdict and explicitly reloads the SPEF written by the recipe.
    """
    if not re.search(r"(?m)^\s*extract_parasitics\b", template):
        raise ValueError(
            "run_sta renderer did not provide extract_parasitics; "
            "post-route RCX capability is required"
        )
    write = re.search(r"(?m)^(\s*write_spef\s+(.+?)\s*)$", template)
    if write is None:
        raise ValueError(
            "run_sta renderer did not provide write_spef; extracted SPEF "
            "evidence is required"
        )
    exit_matches = list(re.finditer(r"(?m)^\s*exit\s*$", template))
    if not exit_matches:
        raise ValueError(
            "run_sta renderer has no final exit; cannot safely append "
            "deterministic extracted-timing reports"
        )

    spef_arg = write.group(2).strip()
    # Loading the persisted file makes the STA provenance explicit rather than
    # relying on undocumented in-memory state after extraction.
    if not re.search(r"(?m)^\s*read_spef\b", template):
        template = (
            template[:write.end()]
            + f"\nread_spef {spef_arg}"
            + template[write.end():]
        )

    final_exit = list(re.finditer(r"(?m)^\s*exit\s*$", template))[-1]
    reports = r'''
# Deterministic post-extraction sign-off evidence.  Keep each concern in a
# separate file so missing or malformed evidence fails closed in the graph.
report_checks -path_delay max -format full_clock_expanded > "$out_dir/setup.rpt"
report_checks -path_delay min -format full_clock_expanded > "$out_dir/hold.rpt"
report_wns > "$out_dir/sta.rpt"
report_tns >> "$out_dir/sta.rpt"
check_setup -verbose > "$out_dir/constraint_checks.rpt"
report_parasitic_annotation -report_unannotated > "$out_dir/parasitic_annotation.rpt"
report_clock_properties [all_clocks] > "$out_dir/clocks.rpt"
report_power > "$out_dir/power.rpt"
report_design_area > "$out_dir/area.rpt"
'''
    return template[:final_exit.start()] + reports + template[final_exit.start():]


def _read_report(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _valid_spef(path: Path) -> bool:
    """Require both a SPEF header and at least one extracted design net."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            header = stream.read(512)
            if "*SPEF" not in header:
                return False
            remainder = header + stream.read()
        return re.search(r"(?m)^\*D_NET\s+", remainder) is not None
    except OSError:
        return False


def _unannotated_driver_evidence(
    report_text: str, routed_def_path: str | Path | None
) -> tuple[int | None, list[str], list[str]]:
    """Classify fully unannotated drivers using routed DEF connectivity.

    A driver output absent from every DEF net is an intentionally unconnected
    output and cannot affect a functional path.  Any driver connected to a DEF
    net is functional extraction coverage missing and blocks sign-off.  When
    the report cannot be reconciled to the DEF, fail closed as unknown.
    """
    match = _UNANNOTATED_BLOCK_RE.search(report_text)
    if not match:
        return None, [], []
    declared = int(match.group(1))
    names = [line.strip() for line in match.group(2).splitlines() if line.strip()]
    if declared == 0:
        return 0, [], []
    if len(names) != declared:
        return declared, [], [
            f"<unreconciled report: declared {declared}, listed {len(names)}>"
        ]
    if routed_def_path is None:
        return declared, [], names
    def_text = _read_report(Path(routed_def_path))
    if def_text is None:
        return declared, [], names

    components_match = re.search(
        r"(?ms)^\s*COMPONENTS\s+(\d+)\s*;(.*?)^\s*END\s+COMPONENTS\s*$",
        def_text,
    )
    nets_match = re.search(
        r"(?ms)^\s*NETS\s+(\d+)\s*;(.*?)^\s*END\s+NETS\s*$",
        def_text,
    )
    if components_match is None or nets_match is None:
        return declared, [], names
    component_names = re.findall(
        r"(?m)^\s*-\s+(\S+)\s+\S+", components_match.group(2)
    )
    net_entries = re.findall(r"(?m)^\s*-\s+\S+", nets_match.group(2))
    if (
        len(component_names) != int(components_match.group(1))
        or len(set(component_names)) != len(component_names)
        or len(net_entries) != int(nets_match.group(1))
    ):
        return declared, [], names
    components = set(component_names)

    unconnected: list[str] = []
    functional_or_unknown: list[str] = []
    for name in names:
        if "/" not in name:
            functional_or_unknown.append(name)
            continue
        instance, pin = name.rsplit("/", 1)
        # DEF escaping and hierarchical instance names need a real DEF parser;
        # do not infer disconnection from a regex normalization guess.
        if (
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", instance) is None
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", pin) is None
            or instance not in components
        ):
            functional_or_unknown.append(name)
            continue
        connected = re.search(
            rf"\(\s*{re.escape(instance)}\s+{re.escape(pin)}\s*\)",
            # Search the full, structurally validated DEF so SPECIALNETS and
            # other legal connection sections cannot hide a functional load.
            def_text,
        )
        if connected:
            functional_or_unknown.append(name)
        else:
            unconnected.append(name)
    return declared, unconnected, functional_or_unknown


def parse_extracted_timing_reports(
    output_dir: str | Path,
    *,
    routed_def_path: str | Path | None = None,
) -> dict[str, Any]:
    """Parse persisted OpenSTA/OpenRCX reports into a fail-closed verdict."""
    out = Path(output_dir)
    paths = {name: out / name for name in _REPORT_NAMES}
    texts = {name: _read_report(path) for name, path in paths.items()}

    missing = [name for name in _REQUIRED_REPORT_NAMES if texts[name] is None]
    setup_text = texts["setup.rpt"] or ""
    hold_text = texts["hold.rpt"] or ""
    sta_text = texts["sta.rpt"] or ""
    constraint_text = texts["constraint_checks.rpt"] or ""
    parasitic_text = texts["parasitic_annotation.rpt"] or ""
    clocks_text = texts["clocks.rpt"] or ""

    setup_slack = _minimum_float(_SLACK_RE, setup_text)
    hold_slack = _minimum_float(_SLACK_RE, hold_text)
    wns = _minimum_float(_WNS_RE, sta_text)
    tns = _minimum_float(_TNS_RE, sta_text)

    # check_setup writes nothing on a clean design in OpenSTA.  Any emitted
    # diagnostic means coverage is incomplete or another constraint is bad;
    # preserve it and fail closed instead of trying to classify prose.
    constraint_diagnostics = [
        line.strip() for line in constraint_text.splitlines() if line.strip()
    ]
    partial_match = _PARTIAL_UNANNOTATED_RE.search(parasitic_text)
    partial_unannotated = int(partial_match.group(1)) if partial_match else None
    (unannotated, proven_unconnected,
     functional_unannotated) = _unannotated_driver_evidence(
        parasitic_text, routed_def_path
    )

    reasons: list[str] = []
    if missing:
        reasons.append("missing report(s): " + ", ".join(missing))
    if not _finite(setup_slack):
        reasons.append("setup slack is absent or non-finite")
    elif setup_slack < 0:
        reasons.append(f"setup slack is negative ({setup_slack:g} ns)")
    if not _finite(hold_slack):
        reasons.append("hold slack is absent or non-finite")
    elif hold_slack < 0:
        reasons.append(f"hold slack is negative ({hold_slack:g} ns)")
    if not _finite(wns):
        reasons.append("WNS is absent or non-finite")
    elif wns < 0:
        reasons.append(f"WNS is negative ({wns:g} ns)")
    if not _finite(tns):
        reasons.append("TNS is absent or non-finite")
    elif tns < 0:
        reasons.append(f"TNS is negative ({tns:g} ns)")
    if constraint_diagnostics:
        reasons.append(
            "constraint coverage is incomplete: "
            + "; ".join(constraint_diagnostics[:5])
        )
    if not clocks_text.strip():
        reasons.append("no clock properties were reported")
    if partial_unannotated is None:
        reasons.append("parasitic annotation completeness is unreported")
    elif partial_unannotated:
        reasons.append(
            f"{partial_unannotated} driver(s) are partially parasitic-annotated"
        )
    if unannotated is None:
        reasons.append("fully unannotated driver count is unreported")
    elif functional_unannotated:
        reasons.append(
            "functional or unproven drivers are wholly unannotated: "
            + ", ".join(functional_unannotated[:10])
        )

    power_text = texts["power.rpt"] or ""
    total_power_w = _last_float(_TOTAL_POWER_RE, power_text)
    area_match = _AREA_RE.search(texts["area.rpt"] or "")
    area_um2 = float(area_match.group(1)) if area_match else None
    utilization_pct = (
        float(area_match.group(2))
        if area_match and area_match.group(2) is not None
        else None
    )

    return {
        "reports_complete": not missing,
        "setup_slack_ns": setup_slack,
        "hold_slack_ns": hold_slack,
        "wns_ns": wns,
        "tns_ns": tns,
        "constraint_diagnostic_count": len(constraint_diagnostics),
        "constraint_diagnostics": constraint_diagnostics,
        "partial_unannotated_drivers": partial_unannotated,
        "unannotated_drivers": unannotated,
        "proven_unconnected_unannotated_drivers": proven_unconnected,
        "functional_unannotated_drivers": functional_unannotated,
        "total_power_mw": total_power_w * 1000.0 if total_power_w is not None else None,
        "design_area_um2": area_um2,
        "utilization_pct": utilization_pct,
        "report_paths": {name.removesuffix(".rpt"): str(path)
                         for name, path in paths.items()},
        "met": not reasons,
        "failure_reasons": reasons,
    }


def _failure(reason: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "met": False,
        "sign_off": "FAIL",
        "source": "extracted_rcx_sta",
        "extraction_complete": False,
        "failure_reasons": [reason],
        "error": reason,
    }
    result.update(extra)
    return result


def run_extracted_timing(
    *,
    block_name: str,
    routed_def_path: str,
    sdc_path: str,
    netlist_path: str = "",
    output_dir: str | Path,
    timeout_s: int,
    deployment: Any | None = None,
) -> dict[str, Any]:
    """Run real deployment-owned RCX+STA and return a deterministic verdict."""
    routed_def = Path(routed_def_path)
    sdc = Path(sdc_path)
    if not routed_def.is_file():
        return _failure(f"routed DEF is missing: {routed_def}")
    if not sdc.is_file():
        return _failure(f"timing constraints (SDC) are missing: {sdc}")

    if deployment is None:
        try:
            from orchestrator.pdk.registry import get_deployment
            deployment = get_deployment()
        except Exception as exc:  # noqa: BLE001
            return _failure(f"active deployment could not be resolved: {exc}")

    tool = deployment.tool("run_sta")
    if tool is None:
        return _failure(
            f"deployment '{deployment.name}' does not implement run_sta; "
            "extracted timing is required"
        )
    renderer = getattr(tool, "render_rcx_tcl", None)
    if not callable(renderer):
        return _failure(
            f"deployment '{deployment.name}' run_sta has no "
            "render_rcx_tcl capability; cannot perform extracted timing"
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    script_path = out / f"rcx_{block_name}.tcl"
    spef_path = out / f"{block_name}.spef"

    # A retry must never adopt reports from an older invocation.
    for stale in (*[out / name for name in _REPORT_NAMES], spef_path):
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            return _failure(f"cannot clear stale timing artifact {stale}: {exc}")

    try:
        template = renderer(
            block_name, str(routed_def.resolve()), str(sdc.resolve())
        )
        script_path.write_text(
            _instrument_rcx_tcl(template), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        return _failure(
            f"deployment '{deployment.name}' could not prepare extracted "
            f"timing: {exc}",
            script_path=str(script_path),
        )

    request = ToolRequest(
        verb="run_sta",
        design=block_name,
        inputs={
            "script": script_path,
            "routed_def": routed_def,
            "sdc": sdc,
        },
        out_dir=out,
        params={"analysis": "post_route_extracted"},
        timeout_s=timeout_s,
    )
    try:
        tool_result = tool.run(request)
    except Exception as exc:  # noqa: BLE001
        return _failure(
            f"deployment run_sta raised during extracted timing: {exc}",
            script_path=str(script_path),
        )

    parsed = parse_extracted_timing_reports(out, routed_def_path=routed_def)
    # Some OpenROAD builds implement report_design_area through the logger, so
    # Tcl redirection does not create area.rpt.  Area is advisory; recover it
    # from the deployment tool log when available without affecting sign-off.
    if parsed.get("design_area_um2") is None:
        log_path = getattr(tool_result, "log_path", None)
        log_text = _read_report(Path(log_path)) if log_path else None
        area_match = _AREA_RE.search(log_text or "")
        if area_match:
            parsed["design_area_um2"] = float(area_match.group(1))
            parsed["utilization_pct"] = (
                float(area_match.group(2))
                if area_match.group(2) is not None
                else None
            )
    reasons = list(parsed["failure_reasons"])
    if not bool(getattr(tool_result, "tool_ok", False)):
        reasons.insert(0, "run_sta did not complete successfully")
    elif not bool(getattr(tool_result, "ok", False)):
        reasons.insert(0, "run_sta returned a failing blocking check")

    extraction_complete = _valid_spef(spef_path)
    if not extraction_complete:
        reasons.insert(0, f"extracted SPEF is missing or invalid: {spef_path}")

    met = not reasons
    liberty_path: Path | None = None
    deployment_liberty = getattr(deployment, "liberty", None)
    if deployment_liberty:
        liberty_path = Path(deployment_liberty)
    else:
        try:
            liberty_path = Path(deployment.pdk.liberty_path())
        except Exception:  # noqa: BLE001 - provenance is best-effort
            liberty_path = None
    artifact_paths = {
        "routed_def": routed_def,
        "sdc": sdc,
        "netlist": Path(netlist_path) if netlist_path else None,
        "liberty": liberty_path,
        "spef": spef_path,
        "script": script_path,
        **{f"report_{name.removesuffix('.rpt')}": out / name
           for name in _REPORT_NAMES},
    }
    provenance = {
        "artifact_paths": {
            name: str(path) for name, path in artifact_paths.items()
            if path is not None
        },
        "sha256": {
            name: digest for name, path in artifact_paths.items()
            if path is not None and (digest := _sha256_file(path)) is not None
        },
    }

    result = {
        **parsed,
        "met": met,
        "sign_off": "PASS" if met else "FAIL",
        "source": "extracted_rcx_sta",
        "extraction_complete": extraction_complete,
        "failure_reasons": reasons,
        "error": "; ".join(reasons),
        "deployment": str(deployment.name),
        "routed_def_path": str(routed_def),
        "sdc_path": str(sdc),
        "spef_path": str(spef_path),
        "script_path": str(script_path),
        "provenance": provenance,
        "tool_log_path": (
            str(tool_result.log_path) if getattr(tool_result, "log_path", None)
            else ""
        ),
        "tool_result": tool_result.to_json(),
        "analysis_scope": (
            "post-route extracted timing at the active deployment's configured "
            "library/RC corner; no multicorner sign-off claim"
        ),
    }
    try:
        (out / "result.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
        )
    except OSError:
        # The in-memory verdict remains authoritative; report paths explain the
        # failed persistence if a caller needs to diagnose the filesystem.
        pass
    return result
