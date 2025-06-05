import unittest
from fastapi.testclient import TestClient
from gateway import app

class TestGateway(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_endpoints(self):
        response = self.client.get("/endpoints")
        self.assertEqual(response.status_code, 200)
        self.assertIn("endpoints", response.json())

    def test_metrics(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("# HELP", response.text)

    def test_process(self):
        data = {"question": "¿Cuál es el horario de atención?"}
        response = self.client.post("/process", json=data, auth=("admin", "admin"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("respuesta", response.json())

if __name__ == "__main__":
    unittest.main()
