"""Exact-source async readiness status contract, with Python stdlib only.

Extract the real endpoint rather than duplicating the implementation. The test
does not import the operational FastAPI app or contact the ledger/service.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / "app" / "api.py"


def _probe(ledger_valid: bool, exemplar_valid: bool) -> tuple[int, str]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    func = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "readyz"
    )
    func.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            func,
        ],
        type_ignores=[],
    )

    class ReadinessResponse:
        def __init__(self, *, status: str, service: str):
            self.status = status
            self.service = service

    ledger = Mock()
    ledger.audit.return_value = SimpleNamespace(valid=ledger_valid)
    exemplar_store = Mock()
    exemplar_store.verify.return_value = (exemplar_valid, 0)
    ns = {
        "ledger": ledger,
        "exemplar_store": exemplar_store,
        "ReadinessResponse": ReadinessResponse,
    }
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), ns)
    response = SimpleNamespace(status_code=200)
    fn = ns["readyz"]
    result = asyncio.run(
        fn(response) if inspect.signature(fn).parameters else fn()
    )
    ledger.audit.assert_called_once_with()
    exemplar_store.verify.assert_called_once_with()
    assert result.service == "gdw"
    return response.status_code, result.status


class ReadinessHTTPContract(unittest.TestCase):
    def test_healthy_state_returns_200(self) -> None:
        self.assertEqual(_probe(True, True), (200, "ready"))

    def test_invalid_receipt_ledger_returns_503(self) -> None:
        self.assertEqual(_probe(False, True), (503, "not_ready"))

    def test_invalid_exemplar_chain_returns_503(self) -> None:
        self.assertEqual(_probe(True, False), (503, "not_ready"))

    def test_both_invalid_return_503(self) -> None:
        self.assertEqual(_probe(False, False), (503, "not_ready"))


if __name__ == "__main__":
    unittest.main()
