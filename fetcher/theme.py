"""Shared design system for every page: one CSS injection, one set of themed
components, instead of each page hand-rolling its own copy-pasted <style> block.

Colors are expressed as CSS custom properties so the whole app stays legible in
both Streamlit's light and dark themes. Where possible we read Streamlit's own
--text-color/--background-color/--secondary-background-color variables (which
track the user's actual active theme, not just OS preference); our own semantic
accents (success/danger/warning/info) are defined here and swapped under
prefers-color-scheme since Streamlit doesn't expose those itself.
"""

import html as _html
from typing import Optional

import streamlit as st

BRAND = "#2563EB"

_CSS = """
<style>
:root {
    --mf-success: #059669;
    --mf-success-bg: rgba(5, 150, 105, 0.12);
    --mf-danger: #DC2626;
    --mf-danger-bg: rgba(220, 38, 38, 0.12);
    --mf-warning: #D97706;
    --mf-warning-bg: rgba(217, 119, 6, 0.12);
    --mf-accent: #2563EB;
    --mf-accent-bg: rgba(37, 99, 235, 0.12);
    --mf-muted: #64748B;
    --mf-border: rgba(128, 128, 128, 0.25);
    --mf-card-bg: rgba(128, 128, 128, 0.04);
}
@media (prefers-color-scheme: dark) {
    :root {
        --mf-success: #34D399;
        --mf-success-bg: rgba(52, 211, 153, 0.14);
        --mf-danger: #F87171;
        --mf-danger-bg: rgba(248, 113, 113, 0.14);
        --mf-warning: #FBBF24;
        --mf-warning-bg: rgba(251, 191, 36, 0.14);
        --mf-accent: #60A5FA;
        --mf-accent-bg: rgba(96, 165, 250, 0.14);
        --mf-muted: #94A3B8;
        --mf-border: rgba(148, 163, 184, 0.28);
        --mf-card-bg: rgba(148, 163, 184, 0.06);
    }
}

.mf-page-title, .main-header, .page-title {
    font-size: 2.1rem;
    font-weight: 800;
    color: var(--text-color, inherit);
    margin-bottom: 2px;
    letter-spacing: -0.01em;
}
.mf-page-caption, .sub-header, .page-caption {
    font-size: 1.02rem;
    color: var(--mf-muted);
    margin-bottom: 1.1rem;
}

/* Metric / KPI card family (shared across every page) */
.metric-card, .quant-card, .status-card {
    background: var(--mf-card-bg);
    border: 1px solid var(--mf-border);
    border-radius: 12px;
    padding: 14px 16px;
    min-height: 120px;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    position: relative;
    overflow: visible !important;
    transition: border-color 0.15s ease;
}
.metric-card:hover, .quant-card:hover {
    border-color: var(--mf-accent);
}
div[data-testid="column"] { overflow: visible !important; }

.metric-title, .quant-title {
    font-size: 0.75rem;
    color: var(--mf-muted);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    line-height: 1.15;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.metric-value, .quant-value {
    font-size: 1.5rem;
    font-weight: 800;
    line-height: 1.2;
    margin: 4px 0;
    color: var(--text-color, inherit);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.metric-value.mf-pos, .quant-value.mf-pos { color: var(--mf-success); }
.metric-value.mf-neg, .quant-value.mf-neg { color: var(--mf-danger); }
.metric-value.mf-warn, .quant-value.mf-warn { color: var(--mf-warning); }

.metric-sub, .quant-sub {
    font-size: 0.8rem;
    color: var(--mf-muted);
    line-height: 1.3;
    height: 34px;
    white-space: normal;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    text-overflow: ellipsis;
}
.metric-sub-neg { color: var(--mf-danger); font-weight: 500; }
.metric-sub-pos { color: var(--mf-success); font-weight: 500; }
.metric-sub-neutral { color: var(--mf-muted); }

/* Status pill (Live / Stale / Insufficient data) */
.mf-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
}
.mf-pill-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.mf-pill-success { background: var(--mf-success-bg); color: var(--mf-success); }
.mf-pill-success .mf-pill-dot { background: var(--mf-success); }
.mf-pill-warning { background: var(--mf-warning-bg); color: var(--mf-warning); }
.mf-pill-warning .mf-pill-dot { background: var(--mf-warning); }
.mf-pill-danger { background: var(--mf-danger-bg); color: var(--mf-danger); }
.mf-pill-danger .mf-pill-dot { background: var(--mf-danger); }
.mf-pill-neutral { background: var(--mf-card-bg); color: var(--mf-muted); }
.mf-pill-neutral .mf-pill-dot { background: var(--mf-muted); }

/* Inline notice banners (used for "data unavailable / excluded" style warnings) */
.mf-banner {
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 0.85rem;
    line-height: 1.5;
    margin: 6px 0 14px 0;
    border: 1px solid transparent;
}
.mf-banner-info { background: var(--mf-accent-bg); color: var(--text-color, inherit); border-color: var(--mf-accent); }
.mf-banner-warning { background: var(--mf-warning-bg); color: var(--text-color, inherit); border-color: var(--mf-warning); }
.mf-banner-danger { background: var(--mf-danger-bg); color: var(--text-color, inherit); border-color: var(--mf-danger); }

.filter-box, .quadrant-box {
    background: var(--mf-card-bg);
    border: 1px solid var(--mf-border);
    border-radius: 10px;
    padding: 12px 16px;
}

/* Hover tooltip / info-icon system (intentionally fixed-dark surface: a
   tooltip popover is legible against either page theme when it inverts) */
.info-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 15px;
    height: 15px;
    border-radius: 50%;
    background-color: rgba(100, 116, 139, 0.25);
    color: var(--mf-muted);
    font-size: 10px;
    font-weight: 700;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-style: normal;
    cursor: help;
    margin-left: 6px;
    position: relative;
    vertical-align: middle;
    text-transform: none;
    letter-spacing: normal;
    line-height: 1;
    transition: all 0.2s ease-in-out;
}
.info-icon:hover {
    background-color: var(--mf-accent);
    color: #FFFFFF;
    box-shadow: 0 0 6px rgba(37, 99, 235, 0.5);
}
.info-icon .tooltip-box {
    visibility: hidden;
    opacity: 0;
    width: 280px;
    background-color: #0F172A;
    color: #F8FAFC;
    text-align: left;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 0.78rem;
    font-weight: 400;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    line-height: 1.45;
    position: absolute;
    z-index: 999999;
    bottom: 140%;
    left: 50%;
    transform: translateX(-50%);
    box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45), 0 4px 10px rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.18);
    pointer-events: none;
    transition: opacity 0.25s cubic-bezier(0.16, 1, 0.3, 1), visibility 0.25s cubic-bezier(0.16, 1, 0.3, 1), transform 0.25s ease;
    white-space: normal;
    word-wrap: break-word;
}
.info-icon .tooltip-box::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 50%;
    margin-left: -6px;
    border-width: 6px;
    border-style: solid;
    border-color: #0F172A transparent transparent transparent;
}
.info-icon.tooltip-align-left .tooltip-box { left: -12px; transform: none; }
.info-icon.tooltip-align-left .tooltip-box::after { left: 18px; margin-left: 0; }
.info-icon.tooltip-align-right .tooltip-box { left: auto; right: -12px; transform: none; }
.info-icon.tooltip-align-right .tooltip-box::after { left: auto; right: 18px; margin-left: 0; }
.info-icon:hover .tooltip-box { visibility: visible; opacity: 1; }

.tooltip-formula {
    display: inline-block;
    background: var(--mf-accent-bg);
    color: var(--mf-accent);
    padding: 2px 6px;
    border-radius: 4px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.72rem;
    margin: 4px 0;
    border: 1px solid var(--mf-border);
}

/* Scheme drill-down link chip, used to jump a row into the Quant page */
.mf-drilldown-link a {
    text-decoration: none;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--mf-accent);
}
.mf-drilldown-link a:hover { text-decoration: underline; }
</style>
"""


def inject_theme() -> None:
    """Injects the shared design-system CSS. Call once near the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_page_header(title: str, caption: str) -> None:
    st.markdown(f'<div class="mf-page-title">{_html.escape(title)}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="mf-page-caption">{_html.escape(caption)}</div>', unsafe_allow_html=True)


def format_signed_pct(value: Optional[float], decimals: int = 4) -> str:
    """The one place that formats a signed percentage, so a negative value never
    renders as the literal double-sign '+-2.1300%' bug found across several pages."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"


def tone_class(value: Optional[float]) -> str:
    """Maps a signed number to the mf-pos/mf-neg/'' tone class for metric-value/quant-value."""
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "mf-pos"
    if v < 0:
        return "mf-neg"
    return ""


def render_metric_card(title: str, value: str, sub: str = "", tone: str = "", sub_tone: str = "neutral") -> None:
    """Renders one KPI card using the shared .metric-card family.
    tone: '' | 'mf-pos' | 'mf-neg' | 'mf-warn' for the value color.
    sub_tone: 'neutral' | 'pos' | 'neg' for the sub-line color.
    """
    sub_class = {"pos": "metric-sub-pos", "neg": "metric-sub-neg"}.get(sub_tone, "metric-sub-neutral")
    tone_attr = f" {tone}" if tone else ""
    safe_value = _html.escape(str(value))
    safe_sub = _html.escape(str(sub))
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-title">{_html.escape(title)}</div>
            <div class="metric-value{tone_attr}" title="{safe_value}">{safe_value}</div>
            <div class="{sub_class}" title="{safe_sub}">{safe_sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_pill(label: str, level: str = "success") -> str:
    """Returns an HTML status pill string ('success' | 'warning' | 'danger' | 'neutral')."""
    cls = {"success": "mf-pill-success", "warning": "mf-pill-warning", "danger": "mf-pill-danger"}.get(level, "mf-pill-neutral")
    return f'<span class="mf-pill {cls}"><span class="mf-pill-dot"></span>{_html.escape(label)}</span>'


def render_banner(message: str, level: str = "info") -> None:
    """A themed inline notice — use this instead of a silently-empty section
    whenever data is missing, excluded, or a fallback happened, so users are
    told a fallback is in effect rather than seeing a confident-looking gap or zero."""
    cls = {"info": "mf-banner-info", "warning": "mf-banner-warning", "danger": "mf-banner-danger"}.get(level, "mf-banner-info")
    st.markdown(f'<div class="mf-banner {cls}">{message}</div>', unsafe_allow_html=True)


def tooltip_icon(text: str, align: str = "center") -> str:
    """Returns an inline 'i' info-icon span with a hover tooltip. `text` may contain
    simple HTML (already used this way elsewhere in the app) and is not escaped."""
    align_cls = {"left": "tooltip-align-left", "right": "tooltip-align-right"}.get(align, "")
    return f'<span class="info-icon {align_cls}">i<span class="tooltip-box">{text}</span></span>'
