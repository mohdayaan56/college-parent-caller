"""Shared CSV fixture so unit tests do not depend on the live data/students.csv."""

from pathlib import Path
from contextlib import contextmanager
import shutil
from uuid import uuid4

from app.config import settings

FIXTURE_CSV = Path(__file__).resolve().parent / "fixtures" / "students.csv"
DEMO_PHONE = "+919876543210"


@contextmanager
def scratch_dir():
    root = Path(__file__).resolve().parent / "_scratch_test"
    path = root / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        if path.resolve().is_relative_to(root.resolve()):
            shutil.rmtree(path)


class CsvFixtureMixin:
    def setUp(self) -> None:
        self._previous_csv = settings.csv_path
        self._previous_language_choice = settings.ask_language_before_identity
        self._previous_ollama_enabled = settings.ollama_enabled
        self._previous_understand_all = settings.llm_understand_every_input
        settings.llm_understand_every_input = False  # legacy renderer and fallback scenarios
        settings.ollama_enabled = False  # unit tests never contact a live model
        # Existing scenario tests exercise the conversation after language choice.
        settings.ask_language_before_identity = False
        settings.csv_path = FIXTURE_CSV

    def tearDown(self) -> None:
        settings.csv_path = self._previous_csv
        settings.ask_language_before_identity = self._previous_language_choice
        settings.ollama_enabled = self._previous_ollama_enabled
        settings.llm_understand_every_input = self._previous_understand_all
