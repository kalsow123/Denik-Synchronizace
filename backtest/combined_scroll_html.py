"""
Jedno HTML: několik Plotly grafů pod sebou (vertikální scroll).
Použití: souhrn equity (2) + měsíční druhy (4) + struktura vln (5).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    import plotly.io as pio
except Exception:  # pragma: no cover
    pio = None


def _figure_fragment(fig: Any, *, include_plotlyjs: bool, div_id: str) -> str:
    return pio.to_html(
        fig,
        include_plotlyjs="cdn" if include_plotlyjs else False,
        full_html=False,
        div_id=div_id,
        config={
            "responsive": True,
            "scrollZoom": True,
            "displaylogo": False,
        },
        default_width="100%",
        default_height="92vh",
    )


def write_scroll_combined_plotly_html(
    out_path: Path | str,
    *,
    page_title: str,
    intro_html: str,
    sections: list[tuple[str, Any | None]],
    footer_html: str = "",
) -> Optional[Path]:
    """
    sections: [(nadpis sekce, plotly.Figure | None), ...]
    None = sekce se přeskočí s krátkou zprávou.
    """
    if pio is None:
        print("[combined-scroll-html] Plotly IO není k dispozici — přeskočeno.")
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='cs'>",
        "<head>",
        "<meta charset='utf-8'/>",
        f"<title>{page_title}</title>",
        "<style>",
        "body { font-family: system-ui, Segoe UI, sans-serif; margin: 0; background: #fafafa; color: #222; }",
        "header { padding: 12px 16px; background: #eceff1; border-bottom: 1px solid #cfd8dc; }",
        "header h1 { margin: 0; font-size: 1.15rem; }",
        "section { border-bottom: 1px solid #cfd8dc; padding: 12px 8px 28px; }",
        "section h2 { margin: 8px 0 12px 12px; font-size: 1.05rem; color: #37474f; }",
        ".plot-wrap { width: 100%; min-height: 640px; }",
        ".skip { padding: 16px 24px; color: #616161; }",
        ".bot-config-summary { padding: 12px 16px 32px; background: #fff; }",
        ".bot-config-summary table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }",
        ".bot-config-summary th, .bot-config-summary td { border: 1px solid #cfd8dc; padding: 6px 10px; text-align: left; vertical-align: top; }",
        ".bot-config-summary th { background: #eceff1; }",
        ".bot-config-summary .hint { color: #546e7a; font-size: 0.82rem; }",
        ".bot-config-summary .note { color: #455a64; margin-bottom: 12px; }",
        ".bot-config-summary .copy-block { margin: 18px 0 8px; }",
        ".bot-config-summary .copy-block-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }",
        ".bot-config-summary .copy-btn { padding: 6px 14px; font-size: 0.88rem; cursor: pointer; "
        "background: #1976d2; color: #fff; border: none; border-radius: 4px; }",
        ".bot-config-summary .copy-btn:hover { background: #1565c0; }",
        ".bot-config-summary .copy-snippet { background: #f5f5f5; padding: 12px 14px; overflow-x: auto; "
        "font-size: 0.82rem; line-height: 1.45; border: 1px solid #cfd8dc; border-radius: 4px; margin: 0; }",
        "</style>",
        "</head><body>",
        "<header>",
        f"<h1>{page_title}</h1>",
        f"<div class='intro'>{intro_html}</div>",
        "</header>",
    ]

    first_js = True
    for i, (heading, fig) in enumerate(sections):
        parts.append("<section>")
        parts.append(f"<h2>{heading}</h2>")
        if fig is None:
            parts.append("<p class='skip'>Tato část není k dispozici (chybí data nebo nebyl zapnutý export vln).</p>")
        else:
            fig.update_layout(
                autosize=True,
                height=960,
                margin=dict(l=52, r=40, t=88, b=56),
            )
            parts.append("<div class='plot-wrap'>")
            parts.append(
                _figure_fragment(
                    fig,
                    include_plotlyjs=first_js,
                    div_id=f"plotly_combined_block_{i}",
                )
            )
            parts.append("</div>")
            first_js = False
        parts.append("</section>")

    if footer_html.strip():
        parts.append(footer_html)

    parts.append("</body></html>")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"  Kombinovaný scroll HTML: {out_path}")
    return out_path
