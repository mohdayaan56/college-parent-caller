from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.phone import phones_match


SUBJECTS = ("maths", "science", "english", "computer")
SUBJECT_ALIASES = {
    "math": "maths",
    "maths": "maths",
    "mathematics": "maths",
    "science": "science",
    "sci": "science",
    "english": "english",
    "eng": "english",
    "computer": "computer",
    "computers": "computer",
    "cs": "computer",
    "it": "computer",
}


@dataclass(frozen=True)
class StudentRecord:
    parent_name: str
    parent_phone: str
    student_id: str
    student_name: str
    class_name: str
    section: str
    marks: dict[str, int]
    total: int
    max_total: int
    percentage: float
    language: str = "en"
    teacher_name: str = ""
    teacher_phone: str = ""
    parent_name_hi: str = ""
    parent_name_kn: str = ""
    student_name_hi: str = ""
    student_name_kn: str = ""

    def subject_score(self, subject: str) -> int | None:
        raw = subject.lower().strip()
        if raw in self.marks:
            return self.marks[raw]
        canonical = SUBJECT_ALIASES.get(raw, raw)
        for key, score in self.marks.items():
            if SUBJECT_ALIASES.get(key, key) == canonical:
                return score
        return None


class CsvError(RuntimeError):
    pass


def _parse_int(value: str, field: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise CsvError(f"Invalid number in CSV field '{field}': {value!r}") from exc


def load_students(path: Path | None = None) -> list[StudentRecord]:
    csv_path = Path(path or settings.csv_path)
    if not csv_path.is_file():
        raise CsvError(f"Student CSV not found: {csv_path}")

    records: list[StudentRecord] = []
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"parent_name", "parent_phone", "student_id", "student_name"}
            if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
                raise CsvError(
                    "CSV is missing required columns: "
                    + ", ".join(sorted(required))
                )
            metadata = required | {
                "class", "section", "language", "teacher_name", "teacher_phone",
                "parent_name_hi", "parent_name_kn", "student_name_hi", "student_name_kn",
            }
            subjects = [col for col in reader.fieldnames if col not in metadata]
            if not subjects:
                raise CsvError("CSV needs at least one subject mark column")
            for line_number, row in enumerate(reader, start=2):
                if any(row.get(field) is None for field in required):
                    raise CsvError(f"Missing required value on CSV line {line_number}")
                marks = {
                    subject.strip().lower(): _parse_int(row[subject], subject)
                    for subject in subjects if str(row.get(subject) or "").strip()
                }
                if not marks:
                    raise CsvError(f"No marks for student {row.get('student_id')}")
                total = sum(marks.values())
                max_total = settings.max_marks_per_subject * len(marks)
                percentage = round((total / max_total) * 100, 2) if max_total else 0.0
                records.append(
                    StudentRecord(
                        parent_name=row["parent_name"].strip(),
                        parent_phone=row["parent_phone"].strip(),
                        student_id=str(row["student_id"]).strip(),
                        student_name=row["student_name"].strip(),
                        class_name=str(row.get("class") or "").strip(),
                        section=str(row.get("section") or "").strip(),
                        marks=marks,
                        total=total,
                        max_total=max_total,
                        percentage=percentage,
                        language=str(row.get("language") or "en").strip().lower(),
                        teacher_name=str(row.get("teacher_name") or "").strip(),
                        teacher_phone=str(row.get("teacher_phone") or "").strip(),
                        parent_name_hi=str(row.get("parent_name_hi") or "").strip(),
                        parent_name_kn=str(row.get("parent_name_kn") or "").strip(),
                        student_name_hi=str(row.get("student_name_hi") or "").strip(),
                        student_name_kn=str(row.get("student_name_kn") or "").strip(),
                    )
                )
    except CsvError:
        raise
    except OSError as exc:
        raise CsvError(f"Could not read student CSV: {exc}") from exc

    if not records:
        raise CsvError("Student CSV has no data rows")
    return records


def find_by_phone(phone: str, path: Path | None = None) -> list[StudentRecord]:
    return [row for row in load_students(path) if phones_match(row.parent_phone, phone)]


def find_by_student_id(student_id: str, path: Path | None = None) -> StudentRecord | None:
    sid = str(student_id).strip()
    for row in load_students(path):
        if row.student_id == sid:
            return row
    return None
