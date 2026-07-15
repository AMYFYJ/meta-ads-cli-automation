"""Report assembly: agent-authored markdown + a deterministic machine appendix.

The appendix is generated in code so the numbers the human approves against
never depend on the LLM transcribing them correctly.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
from pathlib import Path
from typing import Any

from meta_ads_pipeline.models import Dataset
from meta_ads_pipeline.schema import clean

from .analysis import aggregate
from .config import AgentConfig

APPROVAL_INSTRUCTIONS = (
    "**To approve:** open the workbook, set `approval_status` to `APPROVED` on the "
    "BulkChanges rows you accept, save, then run "
    "`python -m ads_agent apply-approved --mode live --yes`."
)


def report_paths(cfg: AgentConfig, date: _dt.date | None = None) -> tuple[Path, Path]:
    stamp = (date or _dt.date.today()).isoformat()
    base = cfg.report_dir / f"{stamp}_scan"
    return base.with_suffix(".md"), base.with_suffix(".html")


def latest_report(cfg: AgentConfig) -> Path | None:
    if not cfg.report_dir.exists():
        return None
    reports = sorted(cfg.report_dir.glob("*_scan.md"))
    return reports[-1] if reports else None


def build_appendix(dataset: Dataset, run_change_ids: list[str], days: int = 7) -> str:
    lines = ["", "---", "", "## Machine appendix (generated deterministically)", ""]
    changes = [
        row for row in dataset.tables.get("BulkChanges", [])
        if clean(row.get("change_id")) in set(run_change_ids)
    ]
    lines.append(f"### BulkChanges rows written this run ({len(changes)})")
    lines.append("")
    if changes:
        lines.append("| change_id | level | object | operation | field | old → new | status | reason |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for row in changes:
            old_new = f"{clean(row.get('old_value')) or '—'} → {clean(row.get('new_value')) or clean(row.get('value_json')) or '—'}"
            lines.append(
                "| " + " | ".join(
                    _cell(value) for value in (
                        row.get("change_id"), row.get("object_level"), row.get("object_key_or_meta_id"),
                        row.get("operation"), row.get("field"), old_new,
                        row.get("approval_status"), row.get("reason"),
                    )
                ) + " |"
            )
    else:
        lines.append("_No new proposals this run._")
    lines.append("")
    agg = aggregate(dataset, days=days)
    lines.append(f"### Raw ad-set aggregates (last {days}d, {agg['snapshot_rows_used']} snapshot rows)")
    lines.append("")
    lines.append("| ad set | campaign | spend | impr | clicks | CTR | CPC | CPM | conv | cost/result | ROAS |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for entry in agg["by_adset"]:
        lines.append(
            "| " + " | ".join(
                _cell(value) for value in (
                    entry["adset_name"] or entry["adset_key"], entry["campaign_name"] or entry["campaign_key"],
                    f"${entry['spend']:.2f}", entry["impressions"], entry["clicks"],
                    f"{entry['ctr']:.2%}", f"${entry['cpc']:.2f}", f"${entry['cpm']:.2f}",
                    entry["conversions"], f"${entry['cost_per_result']:.2f}", entry["roas"] or "—",
                )
            ) + " |"
        )
    totals = agg["totals"]
    lines.append(
        f"| **Total** |  | **${totals['spend']:.2f}** | **{totals['impressions']}** | **{totals['clicks']}** | "
        f"**{totals['ctr']:.2%}** | **${totals['cpc']:.2f}** | **${totals['cpm']:.2f}** | **{totals['conversions']}** | "
        f"**${totals['cost_per_result']:.2f}** | **{totals['roas'] or '—'}** |"
    )
    lines.append("")
    return "\n".join(lines)


def write_report_files(cfg: AgentConfig, markdown: str, date: _dt.date | None = None) -> tuple[Path, Path]:
    md_path, html_path = report_paths(cfg, date)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(_render_html(markdown), encoding="utf-8")
    return md_path, html_path


def fallback_report(cfg: AgentConfig, dataset: Dataset, run_change_ids: list[str], note: str) -> str:
    header = [
        f"# Meta Ads scan — {_dt.date.today().isoformat()}",
        "",
        f"_{note}_",
        "",
        APPROVAL_INSTRUCTIONS,
    ]
    return "\n".join(header) + build_appendix(dataset, run_change_ids)


def _cell(value: Any) -> str:
    return clean(value).replace("|", "\\|").replace("\n", " ") or "—"


# ---------------------------------------------------------------------------
# Minimal markdown -> HTML (headings, tables, lists, bold/italic/code, hr)


def _render_html(markdown: str) -> str:
    body: list[str] = []
    table: list[str] = []
    in_list = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table.append(stripped)
            continue
        if table:
            body.append(_render_table(table))
            table = []
        if stripped.startswith("- "):
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>")
            in_list = False
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            body.append(f"<h{level}>{_inline(stripped[level:].strip())}</h{level}>")
        elif stripped in {"---", "***"}:
            body.append("<hr>")
        else:
            body.append(f"<p>{_inline(stripped)}</p>")
    if table:
        body.append(_render_table(table))
    if in_list:
        body.append("</ul>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Meta Ads scan report</title><style>"
        "body{font-family:-apple-system,Segoe UI,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#1a1a1a}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}"
        "th,td{border:1px solid #d0d0d0;padding:.4rem .6rem;text-align:left}"
        "th{background:#f2f2f2}tr:nth-child(even){background:#fafafa}"
        "code{background:#f2f2f2;padding:.1rem .3rem;border-radius:3px}"
        "</style></head><body>" + "\n".join(body) + "</body></html>"
    )


def _render_table(rows: list[str]) -> str:
    parsed = [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]
    if len(parsed) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in parsed[1]):
        header, data = parsed[0], parsed[2:]
    else:
        header, data = None, parsed
    out = ["<table>"]
    if header:
        out.append("<tr>" + "".join(f"<th>{_inline(cell)}</th>" for cell in header) + "</tr>")
    for row in data:
        out.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped
