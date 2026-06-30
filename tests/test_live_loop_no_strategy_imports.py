"""2F — lint: tenký `runtime/live_loop.py` NESMÍ rozhodovat o WAVE vstupech.

VARIANTA A.txt akce 2F. Po 2F je `run_live_loop` jen orchestrace (polling +
session + IO); JEDINÝ rozhodovač je `BacktestEngine.process_bar` (přes
`LiveEngineSession`). Konkrétně: `infra.orders.send_order` (WAVE entry) se v
`live_loop.py` NESMÍ importovat ani volat — patří do `runtime/live_executor.py`
(pass-through z process_bar) a do `runtime/failed_signals_replay.py` (čistý IO
retry). Ověřeno staticky přes AST (importy + volání), nezávisle na runtime/MT5.
"""
from __future__ import annotations

import ast
import io
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel_path: str) -> str:
    with io.open(os.path.join(_ROOT, rel_path), encoding="utf-8") as f:
        return f.read()


def _imported_names(tree: ast.AST) -> set[str]:
    """Jména navázaná importem (vč. `from x import send_order as y` → 'y')."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _imports_symbol(tree: ast.AST, symbol: str) -> bool:
    """True = modul importuje `symbol` z čehokoli (i pod aliasem na send_order)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == symbol:
                    return True
    return False


def _called_func_names(tree: ast.AST) -> set[str]:
    """Jména volaných funkcí: `f(...)` → 'f', `obj.f(...)` → 'f'."""
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
    return called


def test_live_loop_does_not_import_send_order():
    tree = ast.parse(_read("runtime/live_loop.py"))
    assert not _imports_symbol(tree, "send_order"), (
        "live_loop.py NESMÍ importovat infra.orders.send_order (2F: rozhodnutí "
        "patří do process_bar; send_order jen v live_executor / failed_signals_replay)."
    )
    assert "send_order" not in _imported_names(tree)


def test_live_loop_does_not_call_send_order():
    tree = ast.parse(_read("runtime/live_loop.py"))
    assert "send_order" not in _called_func_names(tree), (
        "live_loop.py NESMÍ volat send_order pro WAVE rozhodnutí (2F tenký loop)."
    )


def test_live_loop_delegates_to_live_engine_session():
    """Pozitivní kontrola: tenký loop deleguje rozhodnutí na process_closed_bars."""
    called = _called_func_names(ast.parse(_read("runtime/live_loop.py")))
    assert "process_closed_bars" in called
    assert "catch_up_missed" in called


def test_send_order_lives_in_allowed_modules():
    """Sanity: send_order zůstává povolen v live_executor a failed_signals_replay."""
    executor_called = _called_func_names(ast.parse(_read("runtime/live_executor.py")))
    replay_tree = ast.parse(_read("runtime/failed_signals_replay.py"))
    assert "send_order" in executor_called
    assert "send_order" in _called_func_names(replay_tree)
