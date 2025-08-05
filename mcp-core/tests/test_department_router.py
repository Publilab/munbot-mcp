import sys
import unittest
from pathlib import Path

# Ensure mcp-core is importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from department_router import DepartmentRouter, get_department_id  # noqa: E402


class TestDepartmentRouter(unittest.TestCase):
    def setUp(self):
        data_path = ROOT.parent / "services" / "llm_docs-mcp" / "documents" / "RAG-depto_info.json"
        self.router = DepartmentRouter(str(data_path))

    def test_match_alias(self):
        q = "email del departamento de coordinacion regional"
        self.assertEqual(get_department_id(q, self.router), "AI-001-contacto")

    def test_no_match_returns_none(self):
        q = "informacion sobre parques y jardines"
        self.assertIsNone(get_department_id(q, self.router))


if __name__ == "__main__":
    unittest.main()
