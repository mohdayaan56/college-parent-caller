"""Durable, single-call-at-a-time CSV campaign runner.

The CLI is the only dispatch surface. Run app.main separately so its ARI
WebSocket can receive the Stasis events for dispatched calls.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import httpx

from app.ari import originate
from app.audio_cache import prepare_call_audio
from app.conversation import CallSession
from app.config import ROOT, settings
from app.csv_store import CsvError, StudentRecord, load_students
from app.phone import digits_only, normalize_phone

DB_PATH = settings.campaign_db_path
TERMINAL = {"completed", "denied", "failed", "no_answer", "opted_out", "skipped_opt_out"}


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS campaign_calls (
          id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
          phone TEXT NOT NULL, parent_name TEXT NOT NULL, records_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued', session_id TEXT, channel_id TEXT,
          detail TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(campaign_id, phone)
        );
        CREATE TABLE IF NOT EXISTS opt_out_phones (
          phone TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    try:
        with db:
            yield db
    finally:
        db.close()


def _groups(path: Path) -> list[tuple[str, str, list[StudentRecord]]]:
    rows = load_students(path)
    grouped: dict[str, tuple[str, list[StudentRecord]]] = {}
    student_ids: set[str] = set()
    for row in rows:
        phone = normalize_phone(row.parent_phone)
        if not 10 <= len(digits_only(phone)) <= 15:
            raise CsvError(f"Invalid parent_phone for student {row.student_id}")
        if not row.student_id or row.student_id in student_ids:
            raise CsvError(f"Missing or duplicate student_id: {row.student_id!r}")
        student_ids.add(row.student_id)
        if not row.parent_name or not row.student_name:
            raise CsvError(f"Missing parent or student name for {row.student_id}")
        if row.language not in {"en", "hi", "kn"}:
            raise CsvError(f"Unsupported language for {row.student_id}: {row.language}")
        if row.language != "en" and not getattr(row, f"parent_name_{row.language}"):
            raise CsvError(f"Missing parent_name_{row.language} for {row.student_id}")
        if any(not 0 <= score <= settings.max_marks_per_subject for score in row.marks.values()):
            raise CsvError(f"Marks outside 0-{settings.max_marks_per_subject} for {row.student_id}")
        if phone in grouped:
            name, wards = grouped[phone]
            if name.casefold() != row.parent_name.casefold():
                raise CsvError(f"One phone has different parent names (student {row.student_id})")
            if wards[0].language != row.language:
                raise CsvError(f"One phone has different languages (student {row.student_id})")
            wards.append(row)
        else:
            grouped[phone] = (row.parent_name, [row])
    return [(phone, name, wards) for phone, (name, wards) in grouped.items()]


def preview(path: Path) -> dict:
    groups = _groups(path)
    return {
        "students": sum(len(wards) for _, _, wards in groups),
        "calls": len(groups),
        "parents": [
            {"name": name, "phone_ending": phone[-4:], "language": wards[0].language,
             "students": [r.student_id for r in wards]}
            for phone, name, wards in groups
        ],
    }


def import_campaign(path: Path, name: str) -> str:
    groups = _groups(path)
    campaign_id = uuid4().hex
    with _db() as db:
        blocked = {row["phone"] for row in db.execute("SELECT phone FROM opt_out_phones")}
        db.execute("INSERT INTO campaigns(id, name) VALUES (?, ?)", (campaign_id, name))
        db.executemany(
            "INSERT INTO campaign_calls(id, campaign_id, phone, parent_name, records_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (uuid4().hex, campaign_id, phone, parent, json.dumps([asdict(r) for r in wards]))
                for phone, parent, wards in groups
            ],
        )
        db.executemany(
            "UPDATE campaign_calls SET status='skipped_opt_out' WHERE campaign_id=? AND phone=?",
            [(campaign_id, phone) for phone in blocked],
        )
    return campaign_id


def records_for_call(call_id: str) -> tuple[str, list[StudentRecord]] | None:
    with _db() as db:
        row = db.execute(
            "SELECT phone, records_json FROM campaign_calls WHERE id=?", (call_id,)
        ).fetchone()
    if row is None:
        return None
    return row["phone"], [StudentRecord(**record) for record in json.loads(row["records_json"])]


def finish_call(call_id: str, status: str, detail: str = "") -> None:
    if status not in TERMINAL:
        raise ValueError(f"Invalid terminal status: {status}")
    with _db() as db:
        if status == "opted_out":
            db.execute(
                "INSERT OR IGNORE INTO opt_out_phones(phone) "
                "SELECT phone FROM campaign_calls WHERE id=?", (call_id,)
            )
            db.execute(
                "UPDATE campaign_calls SET status='skipped_opt_out', updated_at=CURRENT_TIMESTAMP "
                "WHERE status='queued' AND phone=(SELECT phone FROM campaign_calls WHERE id=?)",
                (call_id,),
            )
        db.execute(
            "UPDATE campaign_calls SET status=?, detail=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status IN ('dialing','connected')",
            (status, detail[:500], call_id),
        )


def mark_connected(call_id: str) -> None:
    with _db() as db:
        db.execute(
            "UPDATE campaign_calls SET status='connected', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='dialing'", (call_id,)
        )


def requeue_call(call_id: str) -> bool:
    with _db() as db:
        cursor = db.execute(
            "UPDATE campaign_calls SET status='queued', detail=NULL, channel_id=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('failed','no_answer') "
            "AND phone NOT IN (SELECT phone FROM opt_out_phones)", (call_id,)
        )
        return cursor.rowcount == 1


def campaign_status(campaign_id: str) -> dict:
    with _db() as db:
        campaign = db.execute("SELECT id, name FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if campaign is None:
            raise ValueError("Unknown campaign ID")
        rows = db.execute(
            "SELECT status, COUNT(*) AS count FROM campaign_calls WHERE campaign_id=? GROUP BY status",
            (campaign_id,),
        ).fetchall()
        review = db.execute(
            "SELECT id, phone, status, detail FROM campaign_calls "
            "WHERE campaign_id=? AND status IN ('failed','no_answer','dialing','connected') ORDER BY rowid",
            (campaign_id,),
        ).fetchall()
    return {
        "id": campaign_id, "name": campaign["name"],
        "counts": {r["status"]: r["count"] for r in rows},
        "needs_review": [
            {"call_id": row["id"], "phone_ending": row["phone"][-4:],
             "status": row["status"], "detail": row["detail"]} for row in review
        ],
    }


async def run_campaign(campaign_id: str, limit: int, wait_seconds: int) -> dict:
    if not settings.ari_enabled:
        raise RuntimeError("Set ARI_ENABLED=true before dispatching calls")
    campaign_status(campaign_id)
    with _db() as db:
        queued = db.execute(
            "SELECT records_json FROM campaign_calls WHERE campaign_id=? AND status='queued'",
            (campaign_id,),
        ).fetchall()
    if not queued:
        return campaign_status(campaign_id)
    languages = {json.loads(row["records_json"])[0].get("language", "en") for row in queued}
    if settings.tts_engine == "voiceapi_vits":
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(settings.vits_url.rstrip('/') + '/health', timeout=10)
            response.raise_for_status()
            if not languages.issubset(set(response.json().get('languages', []))):
                raise RuntimeError('VITS service lacks campaign languages')
    elif settings.tts_engine == "local":
        if not shutil.which(settings.piper_bin):
            raise RuntimeError("Piper binary is missing")
        if "en" in languages and not settings.piper_model.is_file():
            raise RuntimeError("English Piper voice is missing")
        if "hi" in languages and not settings.piper_hindi_model.is_file():
            raise RuntimeError("Hindi Piper voice is missing")
        if "kn" in languages:
            if not (settings.mms_kannada_model / "model.safetensors").is_file():
                raise RuntimeError("Kannada MMS voice is missing")
            if any(importlib.util.find_spec(module) is None for module in ("torch", "transformers")):
                raise RuntimeError("Kannada MMS Python dependencies are missing")
    elif settings.tts_engine == "piper":
        if languages - {"en"}:
            raise RuntimeError("Hindi/Kannada calls need TTS_ENGINE=indic_parler")
        if not settings.piper_model.is_file() or not shutil.which(settings.piper_bin):
            raise RuntimeError("Piper binary or voice model is missing")
    elif settings.tts_engine == "indic_parler":
        if not (settings.indic_parler_model / "model.safetensors").is_file():
            raise RuntimeError("Local Indic Parler TTS model is missing")
        if not (settings.indic_parler_description_tokenizer / "tokenizer_config.json").is_file():
            raise RuntimeError("Local Indic Parler description tokenizer is missing")
        if any(importlib.util.find_spec(module) is None for module in ("torch", "transformers", "parler_tts")):
            raise RuntimeError("Indic Parler TTS Python dependencies are missing")
    else:
        raise RuntimeError("A real TTS engine is required before dispatch")
    if settings.stt_engine == "meta_omnilingual":
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(settings.meta_stt_url.rstrip('/') + '/health', timeout=10)
            response.raise_for_status()
    elif settings.stt_engine == "gemma_audio":
        if not all(path.is_file() for path in (settings.gemma_runner, settings.gemma_model, settings.gemma_mmproj)):
            raise RuntimeError("Gemma audio runner, GGUF and mmproj must be installed before batch calls")
    elif settings.stt_engine == "whisper_unified":
        if not Path(settings.whisper_model).is_dir() or str(settings.whisper_model).endswith(".en"):
            raise RuntimeError("WHISPER_MODEL must be a local multilingual checkpoint for all three languages")
    elif settings.stt_engine == "indic_conformer":
        if not settings.indic_conformer_model.is_dir():
            raise RuntimeError("Local IndicConformer ASR model is missing")
        if any(importlib.util.find_spec(module) is None for module in ("torch", "torchaudio", "transformers")):
            raise RuntimeError("IndicConformer Python dependencies are missing")
        if "en" in languages and not Path(settings.whisper_model).is_dir():
            raise RuntimeError("WHISPER_MODEL must point to a local English model directory")
    elif settings.stt_engine == "whisper":
        if "en" in languages and not Path(settings.whisper_model).is_dir():
            raise RuntimeError("WHISPER_MODEL must point to a local English model directory")
        if "hi" in languages and not Path(settings.whisper_multilingual_model).is_dir():
            raise RuntimeError("WHISPER_MULTILINGUAL_MODEL must point to a local model directory")
        if "kn" in languages and not Path(settings.whisper_kannada_model or settings.whisper_multilingual_model).is_dir():
            raise RuntimeError("WHISPER_KANNADA_MODEL must point to a local model directory")
    else:
        raise RuntimeError(f"Unsupported STT_ENGINE for batch calls: {settings.stt_engine}")
    async with httpx.AsyncClient(auth=(settings.ari_user, settings.ari_password)) as client:
        response = await client.get(
            f"{settings.ari_url.rstrip('/')}/applications/{settings.ari_app}", timeout=5
        )
        response.raise_for_status()  # The app.main WebSocket must be connected.
    sent = 0
    while sent < limit:
        with _db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT COUNT(*) FROM campaign_calls WHERE status IN ('dialing','connected')"
            ).fetchone()[0]
            if active:
                raise RuntimeError("Another call is active or requires manual review before dispatch")
            row = db.execute(
                "SELECT id, phone FROM campaign_calls WHERE campaign_id=? AND status='queued' "
                "AND phone NOT IN (SELECT phone FROM opt_out_phones) "
                "ORDER BY rowid LIMIT 1", (campaign_id,)
            ).fetchone()
            if row is None:
                break
            call_id, phone = row["id"], row["phone"]
            db.execute(
                "UPDATE campaign_calls SET status='dialing', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (call_id,),
            )
        try:
            snapshot = records_for_call(call_id)
            if snapshot is None:
                raise RuntimeError("Campaign student snapshot unavailable")
            await asyncio.to_thread(prepare_call_audio, CallSession(expected_phone=phone, records=snapshot[1]))
            channel = await originate(phone, f"campaign:{call_id}")
            with _db() as db:
                db.execute(
                    "UPDATE campaign_calls SET channel_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (channel.get("id"), call_id),
                )
        except Exception as exc:
            finish_call(call_id, "failed", str(exc))
            print(f"Call ending {phone[-4:]} failed: {exc}")
            break  # Asterisk is probably unavailable; avoid failing the whole batch.
        sent += 1
        print(f"Dispatched call {sent}; phone ending {phone[-4:]}")
        deadline = time.monotonic() + wait_seconds
        started = time.monotonic()
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            with _db() as db:
                status = db.execute(
                    "SELECT status FROM campaign_calls WHERE id=?", (call_id,)
                ).fetchone()["status"]
            if status in TERMINAL:
                break
            if status == "dialing" and time.monotonic() - started > 50 and channel.get("id"):
                async with httpx.AsyncClient(auth=(settings.ari_user, settings.ari_password)) as client:
                    check = await client.get(
                        f"{settings.ari_url.rstrip('/')}/channels/{channel['id']}", timeout=5
                    )
                if check.status_code == 404:
                    finish_call(call_id, "no_answer", "Channel ended before StasisStart")
                    break
        else:
            print("Call is still active or unanswered; dispatch paused to avoid overlap.")
            break
    return campaign_status(campaign_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview, import, and dispatch a parent call campaign")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preview").add_argument("csv", type=Path)
    imp = sub.add_parser("import")
    imp.add_argument("csv", type=Path)
    imp.add_argument("--name", required=True)
    run = sub.add_parser("run")
    run.add_argument("campaign_id")
    run.add_argument("--execute", action="store_true", help="Place real calls")
    run.add_argument("--limit", type=int, default=1)
    run.add_argument("--wait-seconds", type=int, default=180)
    retry = sub.add_parser("requeue")
    retry.add_argument("call_id")
    retry.add_argument("--execute", action="store_true", help="Allow this reviewed call to be retried")
    sub.add_parser("status").add_argument("campaign_id")
    args = parser.parse_args()
    if args.command == "preview":
        print(json.dumps(preview(args.csv), indent=2))
    elif args.command == "import":
        print(json.dumps({"campaign_id": import_campaign(args.csv, args.name)}, indent=2))
    elif args.command == "status":
        print(json.dumps(campaign_status(args.campaign_id), indent=2))
    elif args.command == "requeue":
        if not args.execute:
            raise SystemExit("Review the call outcome, then add --execute to requeue it")
        print(json.dumps({"requeued": requeue_call(args.call_id)}))
    elif args.command == "run":
        if not args.execute:
            raise SystemExit("Add --execute to place real calls. Import and preview first.")
        if args.limit < 1 or args.wait_seconds < 1:
            raise SystemExit("--limit and --wait-seconds must be positive")
        print(json.dumps(asyncio.run(run_campaign(args.campaign_id, args.limit, args.wait_seconds)), indent=2))


if __name__ == "__main__":
    main()
