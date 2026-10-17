import unittest
import asyncio
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.campaign import campaign_status, finish_call, import_campaign, preview, records_for_call, run_campaign
from app.csv_store import CsvError
from tests.support import scratch_dir


HEADER = "parent_name,parent_phone,student_id,student_name,class,section,maths,science,english,computer\n"


class CampaignTests(unittest.TestCase):
    def test_groups_siblings_and_snapshots_records(self):
        with scratch_dir() as root:
            csv_path = root / "students.csv"
            csv_path.write_text(
                HEADER
                + "Asha Rao,+919876543210,1,Dev Rao,10,A,80,90,70,60\n"
                + "Asha Rao,+919876543210,2,Anu Rao,8,B,75,85,95,65\n",
                encoding="utf-8",
            )
            copy_path = root / "copy.csv"
            copy_path.write_bytes(csv_path.read_bytes())
            with patch("app.campaign.DB_PATH", root / "campaigns.sqlite3"):
                self.assertEqual(preview(csv_path)["calls"], 1)
                campaign_id = import_campaign(csv_path, "test")
                waiting_id = import_campaign(copy_path, "already queued")
                self.assertEqual(campaign_status(campaign_id)["counts"], {"queued": 1})
                import sqlite3

                with closing(sqlite3.connect(root / "campaigns.sqlite3")) as db:
                    call_id = db.execute(
                        "SELECT id FROM campaign_calls WHERE campaign_id=?", (campaign_id,)
                    ).fetchone()[0]
                csv_path.unlink()
                phone, records = records_for_call(call_id)
                self.assertEqual(phone, "+919876543210")
                self.assertEqual([r.student_id for r in records], ["1", "2"])
                with closing(sqlite3.connect(root / "campaigns.sqlite3")) as db:
                    db.execute("UPDATE campaign_calls SET status='dialing' WHERE id=?", (call_id,))
                    db.commit()
                finish_call(call_id, "opted_out")
                second = import_campaign(copy_path, "other")
                self.assertEqual(campaign_status(campaign_id)["counts"], {"opted_out": 1})
                self.assertEqual(campaign_status(waiting_id)["counts"], {"skipped_opt_out": 1})
                self.assertEqual(campaign_status(second)["counts"], {"skipped_opt_out": 1})

    def test_rejects_different_parent_names_at_one_phone(self):
        with scratch_dir() as root:
            csv_path = root / "students.csv"
            csv_path.write_text(
                HEADER
                + "Asha Rao,+919876543210,1,Dev Rao,10,A,80,90,70,60\n"
                + "Raj Rao,+919876543210,2,Anu Rao,8,B,75,85,95,65\n",
                encoding="utf-8",
            )
            with self.assertRaises(CsvError):
                preview(csv_path)

    def test_hindi_campaign_needs_native_name_and_accepts_variable_subjects(self):
        with scratch_dir() as root:
            csv_path = root / "college.csv"
            csv_path.write_text(
                "parent_name,parent_name_hi,parent_phone,student_id,student_name,language,Mathematics,Electronics,Physics\n"
                "Asha Rao,आशा राव,+919876543210,1,Dev Rao,hi,80,90,70\n", encoding="utf-8"
            )
            self.assertEqual(preview(csv_path)["calls"], 1)
            self.assertEqual(preview(csv_path)["parents"][0]["language"], "hi")

    def test_dispatch_one_call_and_do_not_repeat_completed_call(self):
        with scratch_dir() as root:
            csv_path = root / "students.csv"
            csv_path.write_text(
                HEADER + "Asha Rao,+919876543210,1,Dev Rao,10,A,80,90,70,60\n",
                encoding="utf-8",
            )
            model = root / "whisper"
            model.mkdir()
            voice = root / "english.onnx"
            voice.write_bytes(b"model")

            class FakeClient:
                def __init__(self, **_kwargs):
                    pass

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    pass

                async def get(self, *_args, **_kwargs):
                    class Response:
                        def raise_for_status(self):
                            pass

                    return Response()

            called = []

            async def fake_originate(phone, session_id):
                called.append(phone)
                finish_call(session_id.split(":", 1)[1], "completed")
                return {"id": "test-channel"}

            from app import campaign
            with patch("app.campaign.DB_PATH", root / "campaigns.sqlite3"), \
                 patch.object(campaign.settings, "ari_enabled", True), \
                 patch.object(campaign.settings, "whisper_model", str(model)), \
                 patch.object(campaign.settings, "stt_engine", "whisper"), \
                 patch.object(campaign.settings, "tts_engine", "local"), \
                 patch.object(campaign.settings, "piper_model", voice), \
                 patch.object(campaign.settings, "piper_bin", "fake-piper"), \
                 patch("app.campaign.shutil.which", return_value="fake-piper"), \
                 patch("app.campaign.httpx.AsyncClient", FakeClient), \
                 patch("app.campaign.prepare_call_audio", return_value={"prepared": 1}), patch("app.campaign.originate", fake_originate):
                campaign_id = import_campaign(csv_path, "test")
                self.assertEqual(asyncio.run(run_campaign(campaign_id, 1, 5))["counts"],
                                 {"completed": 1})
                asyncio.run(run_campaign(campaign_id, 1, 5))
                self.assertEqual(called, ["+919876543210"])


if __name__ == "__main__":
    unittest.main()
