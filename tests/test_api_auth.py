import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


class ApiAuthTests(unittest.TestCase):
    def test_unprepared_call_is_rejected_without_dialing(self):
        originate = AsyncMock()
        with patch.object(settings, "admin_api_token", "test-secret"), \
             patch.object(settings, "require_prepared_call_audio", True), \
             patch("app.main.call_audio_status", return_value={"ready": False, "missing": 3, "required": 3}), \
             patch("app.main.originate", originate):
            response = TestClient(app).post("/calls/originate", json={"phone": "+919876543210"},
                                           headers={"X-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 409)
        originate.assert_not_called()

    def test_prepare_endpoint_never_dials(self):
        originate = AsyncMock()
        with patch.object(settings, "admin_api_token", "test-secret"), \
             patch("app.main.prepare_call_audio", return_value={"prepared": 22}) as prepare, \
             patch("app.main.originate", originate):
            response = TestClient(app).post("/calls/prepare", json={"phone": "+919876543210"},
                                           headers={"X-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 200)
        prepare.assert_called_once()
        originate.assert_not_called()

    def test_introduction_is_ready_before_dialing(self):
        events = []
        def prepare(session):
            events.append("prepare")
        async def originate(phone, session_id):
            events.append("dial")
            return {"id": "test-channel"}
        with patch.object(settings, "admin_api_token", "test-secret"), \
             patch("app.main.call_audio_status", return_value={"ready": True}), patch("app.main.prepare_greeting", prepare), patch("app.main.originate", originate):
            response = TestClient(app).post("/calls/originate", json={"phone": "+919876543210"},
                                           headers={"X-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events, ["prepare", "dial"])

    def test_failed_introduction_does_not_place_call(self):
        originate = AsyncMock()
        with patch.object(settings, "admin_api_token", "test-secret"), \
             patch("app.main.call_audio_status", return_value={"ready": True}), patch("app.main.prepare_greeting", side_effect=RuntimeError("TTS unavailable")), \
             patch("app.main.originate", originate):
            response = TestClient(app).post("/calls/originate", json={"phone": "+919876543210"},
                                           headers={"X-Admin-Token": "test-secret"})
        self.assertEqual(response.status_code, 502)
        originate.assert_not_called()

    def test_network_bound_session_api_requires_token(self):
        client = TestClient(app)
        with patch.object(settings, "app_host", "0.0.0.0"), patch.object(settings, "admin_api_token", "test-secret"):
            self.assertEqual(client.post("/sessions", json={"phone": "+919876543210"}).status_code, 401)
            self.assertEqual(
                client.post("/sessions", json={"phone": "+919876543210"},
                            headers={"X-Admin-Token": "test-secret"}).status_code,
                200,
            )


if __name__ == "__main__":
    unittest.main()
