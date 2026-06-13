"""
HTML souhrn nastavení BotConfig pro scroll export (projected risk po brokerech).

DŮLEŽITÉ — synchronizace s live botem:
  Přidáte-li nové pole do `config/bot_config.py` (třída BotConfig), promítne se sem
  automaticky (dataclasses.fields). U nových polí doplňte _FIELD_HINTS.
"""
from __future__ import annotations

import dataclasses
import enum
from html import escape
from typing import Any, Dict, Mapping, Optional

from config.bot_config import BotConfig

# V tabulce se nezobrazuje (bot_name je v názvu souboru / combo).
_SKIP_TABLE_FIELDS = frozenset({"bot_name", "bos_reentry_enabled"})

# Pole, u kterých se do sloupců brokerů doplní projected risk (headroom × základ).
_RISK_SCALE_FIELDS = frozenset({"risk_usd", "pp_risk_usd"})

_FIELD_HINTS: dict[str, str] = {
    "risk_usd": "Risk USD na obchod — sloupce brokerů = max_risk_per_trade_usd (projected)",
    "pp_risk_usd": "PP risk — škáluje stejným headroom jako risk_usd",
    "wave_min_sl": "Minimální SL pro standardní WAVE pozice (% od entry ceny)",
    "bos_entry_enable": "Povolí BOS entry market po BOS flipu",
    "bos_entry_in_rrr_fixed": "WAVE_BOS po BOS flipu jen v tp_mode rrr_fixed (bez BOS exitu)",
    "tp_mode": "Režim TP / výstupu",
    "abort_fib_level": (
        "None | float (pasionka mezi fib a SL) | deep_retrace_shift_sl / shift_sl "
        "(rozšíření: vstup s posunutým SL místo skip — viz POPIS .txt)"
    ),
}


def _fmt_value(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, enum.Enum):
        return v.name
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return str(v)


def _python_repr(v: Any) -> str:
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, enum.Enum):
        return f"{v.__class__.__name__}.{v.name}"
    if isinstance(v, str):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return repr(list(v))
    return repr(v)


def _projected_risk_fields(
    cfg: BotConfig,
    max_risk_usd: float,
    headroom_scale: float,
) -> dict[str, Any]:
    """Hodnoty BotConfig s risk_usd / pp_risk_usd podle projected max risk."""
    h = float(headroom_scale) if headroom_scale else 1.0
    max_r = float(max_risk_usd)
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        if not f.init or f.name in _SKIP_TABLE_FIELDS:
            continue
        val = getattr(cfg, f.name)
        if f.name == "risk_usd":
            out[f.name] = round(max_r, 2)
        elif f.name == "pp_risk_usd":
            try:
                base_pp = float(val)
            except (TypeError, ValueError):
                base_pp = float(cfg.risk_usd)
            out[f.name] = round(base_pp * h, 2)
        else:
            out[f.name] = val
    return out


def build_config_copy_snippet(
    cfg: BotConfig,
    broker: str,
    max_risk_usd: float,
    headroom_scale: float,
) -> str:
    """Python blok pro vložení do config/bot_config.py (bez bot_name)."""
    projected = _projected_risk_fields(cfg, max_risk_usd, headroom_scale)
    lines = [
        f"# {broker} — projected @ max_risk_per_trade ({max_risk_usd:,.0f} USD, headroom={headroom_scale:.4g})",
        "# Zkopírujte do BotConfig(...) v config/bot_config.py nebo do grid profilu.",
        "BotConfig(",
    ]
    for f in dataclasses.fields(cfg):
        if not f.init or f.name in _SKIP_TABLE_FIELDS:
            continue
        val = projected.get(f.name, getattr(cfg, f.name))
        lines.append(f"    {f.name}={_python_repr(val)},")
    lines.append(")")
    return "\n".join(lines)


def build_bot_config_summary_html(
    cfg: BotConfig,
    brokers: Optional[Mapping[str, Dict[str, Any]]] = None,
) -> str:
    """
    Tabulka BotConfig + kopírovatelné bloky po brokerovi (projected risk).
    brokers: { "FTMO": { max_risk_per_trade_usd, headroom_scale, ... }, ... }
    """
    broker_names = list(brokers.keys()) if brokers else []
    header = "<tr><th>Parametr</th><th>Základ (backtest)</th>"
    for b in broker_names:
        header += f"<th>{escape(b)}<br><span class='hint'>projected risk</span></th>"
    header += "</tr>"

    body_rows: list[str] = []
    for f in dataclasses.fields(cfg):
        if not f.init or f.name in _SKIP_TABLE_FIELDS:
            continue
        base_val = getattr(cfg, f.name)
        hint = _FIELD_HINTS.get(f.name, "")
        hint_cell = f"<br><span class='hint'>{escape(hint)}</span>" if hint else ""
        cells = f"<td><code>{escape(f.name)}</code>{hint_cell}</td>"
        cells += f"<td>{escape(_fmt_value(base_val))}</td>"

        for b in broker_names:
            bm = brokers[b] if brokers else {}
            h = float(bm.get("headroom_scale", 1.0) or 1.0)
            max_r = bm.get("max_risk_per_trade_usd")
            if f.name in _RISK_SCALE_FIELDS and max_r is not None:
                if f.name == "risk_usd":
                    cells += f"<td><b>{escape(_fmt_value(round(float(max_r), 2)))}</b></td>"
                elif f.name == "pp_risk_usd":
                    try:
                        pp_proj = round(float(base_val) * h, 2)
                    except (TypeError, ValueError):
                        pp_proj = round(float(max_r), 2)
                    cells += f"<td><b>{escape(_fmt_value(pp_proj))}</b></td>"
                else:
                    cells += f"<td>{escape(_fmt_value(base_val))}</td>"
            else:
                cells += f"<td>{escape(_fmt_value(base_val))}</td>"
        body_rows.append(f"<tr>{cells}</tr>")

    copy_blocks: list[str] = []
    if brokers:
        copy_blocks.append(
            "<p class='note'>Zkopírujte blok pro vybraného brokera a vložte do "
            "<code>config/bot_config.py</code> (nebo do grid profilu v "
            "<code>backtest/grid/backtest_conf.py</code>).</p>"
        )
        for b in broker_names:
            bm = brokers[b]
            max_r = float(bm.get("max_risk_per_trade_usd") or cfg.risk_usd)
            h = float(bm.get("headroom_scale", 1.0) or 1.0)
            snippet = build_config_copy_snippet(cfg, b, max_r, h)
            block_id = f"copy-snippet-{b.replace(' ', '_')}"
            copy_blocks.append(
                f"<div class='copy-block'>"
                f"<div class='copy-block-head'>"
                f"<strong>{escape(b)}</strong> "
                f"<span class='hint'>(risk_usd={max_r:,.0f}, headroom={h:.4g})</span> "
                f"<button type='button' class='copy-btn' data-target='{block_id}'>Kopírovat pro Cursor</button>"
                f"</div>"
                f"<pre id='{block_id}' class='copy-snippet'>{escape(snippet)}</pre>"
                f"</div>"
            )

    copy_script = """
<script>
document.querySelectorAll('.copy-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    var id = btn.getAttribute('data-target');
    var el = document.getElementById(id);
    if (!el) return;
    var text = el.innerText || el.textContent;
    function ok() { btn.textContent = 'Zkopírováno'; setTimeout(function(){ btn.textContent = 'Kopírovat pro Cursor'; }, 2000); }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok).catch(function() { fallback(); });
    } else { fallback(); }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); ok(); } catch(e) {}
      document.body.removeChild(ta);
    }
  });
});
</script>
"""

    return (
        "<section class='bot-config-summary'>"
        "<h2>6) Souhrn nastavení bota (BotConfig) — projected po brokerovi</h2>"
        "<p class='note'>Sloupce brokerů: <b>risk_usd</b> / <b>pp_risk_usd</b> po projected "
        "výpočtu (<code>max_risk_per_trade_usd</code>). Ostatní parametry stejné jako základ. "
        "Pole <code>bot_name</code> zde není — viz combo / název souboru.</p>"
        f"<table><thead>{header}</thead><tbody>{''.join(body_rows)}</tbody></table>"
        + "".join(copy_blocks)
        + copy_script
        + "</section>"
    )
