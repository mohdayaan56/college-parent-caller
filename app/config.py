import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    csv_path: Path = ROOT / "data" / "students.csv"
    college_name: str = "the college"
    recordings_dir: Path = ROOT / "recordings"
    campaign_db_path: Path = ROOT / 'data' / 'campaigns.sqlite3'
    max_marks_per_subject: int = 100
    name_match_threshold: float = 0.72
    max_verify_attempts: int = 2
    ask_language_before_identity: bool = True
    language_menu_keypad_only: bool = False
    phone_menu_diagnostics: bool = False
    record_max_seconds: int = Field(default=12, ge=3, le=60)
    record_silence_seconds: int = Field(default=1, ge=1, le=5)
    require_prepared_call_audio: bool = True
    prepare_query_audio: bool = True
    streaming_stt_enabled: bool = False
    streaming_audio_bind_host: str = '0.0.0.0'
    streaming_audio_advertise_host: str = '127.0.0.1'
    streaming_audio_port: int = Field(default=20100, ge=1024, le=65000)
    streaming_audio_ports: int = Field(default=4, ge=1, le=16)
    streaming_endpoint_seconds: float = Field(default=1.0, ge=0.5, le=3.0)
    streaming_partial_min_seconds: float = Field(default=4.0, ge=1.0, le=12.0)
    streaming_partial_step_seconds: float = Field(default=3.0, ge=1.0, le=12.0)
    streaming_partial_max_requests: int = Field(default=1, ge=0, le=4)
    streaming_prefetch_llm: bool = False
    barge_in_vad_threshold: float = Field(default=0.75, ge=0.5, le=0.95)
    barge_in_min_speech_seconds: float = Field(default=0.32, ge=0.16, le=0.8)

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_timeout_seconds: float = 8.0
    ollama_keep_alive: str = "15m"
    ollama_num_ctx: int = Field(default=4096, ge=512, le=8192)
    ollama_num_threads: int = Field(default=4, ge=1, le=16)
    ollama_num_predict: int = Field(default=96, ge=32, le=256)
    ollama_think: bool | None = None
    ollama_enabled: bool = False
    llm_generate_replies: bool = False
    llm_understand_every_input: bool = True

    whisper_model: str = "base.en"
    whisper_multilingual_model: str = ""
    whisper_kannada_model: str = ""
    stt_engine: str = "whisper"
    indic_conformer_model: Path = ROOT / "models" / "indic-conformer-600m-multilingual"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    gemma_runner: Path = ROOT / "tools" / "llama-b11304" / (
        "llama-mtmd-cli.exe" if os.name == "nt" else "llama-mtmd-cli"
    )
    gemma_model: Path = ROOT / "models" / "gemma-4-E2B-it-qat-q4_0-gguf" / "gemma-4-E2B_q4_0-it.gguf"
    gemma_mmproj: Path = ROOT / "models" / "gemma-4-E2B-it-qat-q4_0-gguf" / "gemma-4-E2B-it-mmproj.gguf"
    gemma_timeout_seconds: float = 120.0
    meta_stt_url: str = "http://127.0.0.1:8010"
    meta_stt_timeout_seconds: float = 120.0

    tts_engine: str = "piper"
    vits_url: str = "http://127.0.0.1:8012"
    vits_timeout_seconds: float = 90.0
    piper_bin: str = "piper"
    piper_model: Path = ROOT / "models" / "en_US-lessac-medium.onnx"
    piper_hindi_model: Path = ROOT / "models" / "hi_IN-rohan-medium.onnx"
    mms_kannada_model: Path = ROOT / "models" / "mms-tts-kan"
    indic_parler_model: Path = ROOT / "models" / "indic-parler-tts"
    indic_parler_description_tokenizer: Path = ROOT / "models" / "indic-parler-description-tokenizer"
    kokoro_voice: str = "af_heart"
    tts_sample_rate: int = 16000
    tts_speaking_rate: float = Field(default=0.95, ge=0.85, le=1.15)

    ari_enabled: bool = False
    ari_url: str = "http://127.0.0.1:8088/ari"
    ari_user: str = "asterisk"
    ari_password: str = "change-me"
    ari_app: str = "parent-marks"
    asterisk_sounds_dir: Path = Path("/var/lib/asterisk/sounds/parent-marks")
    sip_endpoint: str = "PJSIP"
    sip_trunk: str = "provider"
    sip_dial_format: str = "e164_digits"
    outbound_channel_driver: str = "pjsip"
    mobile_device: str = "samsung"
    outbound_caller_id: str = '"College" <1000>'

    agi_host: str = "127.0.0.1"
    agi_port: int = 4573

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    admin_api_token: str = ""


settings = Settings()
settings.recordings_dir.mkdir(parents=True, exist_ok=True)
