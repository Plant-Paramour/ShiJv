from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class SamplingModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    max_new_tokens: int = Field(default=4096, gt=0)
    temperature: float = Field(default=0.6, gt=0)
    top_p: float = Field(default=0.95, gt=0, le=1)
    top_k: int = Field(default=20, ge=0)
    min_p: float = Field(default=0.0, ge=0, le=1)

class RewriteSamplingModel(SamplingModel):
    temperature: float = Field(default=0.8, gt=0)

class AgentImageModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='图片', max_length=255)
    data_url: str = Field(max_length=12000000)

    @field_validator('data_url')
    @classmethod
    def validate_image(cls, value):
        import base64
        import re
        match = re.fullmatch('data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)', value)
        if not match:
            raise ValueError('仅支持 PNG、JPEG、WebP 图片')
        try:
            data = base64.b64decode(match[2], validate=True)
        except ValueError as exc:
            raise ValueError('图片编码无效') from exc
        valid = {'png': data.startswith(b'\x89PNG\r\n\x1a\n'), 'jpeg': data.startswith(b'\xff\xd8\xff'), 'webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP'}
        if not data or len(data) > 8 * 1024 * 1024 or (not valid[match[1]]):
            raise ValueError('图片格式无效或超过 8MB')
        return value

class AgentChatModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=12000)
    session_id: str | None = Field(default=None, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=120)
    parent_message_id: str | None = Field(default=None, max_length=128)
    turn_id: str | None = Field(default=None, max_length=128)
    images: list[AgentImageModel] = Field(default_factory=list, max_length=1)

class AgentProposalSubmitModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conversation_id: str = Field(min_length=1, max_length=128)
    requirement: str = Field(max_length=12000)
    candidate_count: int = Field(default=1, ge=1, le=50)
    meter_type: str
    form_name: str
    variant_name: str | None = None
    rhyme_dict_name: str = 'Xinyun'
    rhyme_mode: str | None = None
    rhyme_parts: dict[str, str] | None = None
    fixed_lines: dict[str, str] = Field(default_factory=dict)
    sentence_inputs: dict[str, str] | None = None
    target_line_numbers: list[int] | None = Field(default=None, min_length=1, max_length=64)
    theme: str | None = None
    strict_polyphonic: bool = False
    num_lines: int | None = Field(default=None, ge=4, le=128)
    task_options: dict[str, Any] = Field(default_factory=dict)

class CandidateEvaluationModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    evaluation: dict[str, Any]

class ProfilePatchModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str | None = Field(default=None, max_length=80)
    bio: str | None = Field(default=None, max_length=500)
    notify_on_reaction: bool | None = None
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=254)

class ConversationCreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='新建对话', max_length=15)

class ConversationAutoCreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)

class ConversationPatchModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, max_length=15)
    folder_id: str | None = Field(default=None, max_length=128)
    clear_folder: bool = False

class ConversationMessageEditModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=12000)

class ConversationRestrictionModel(BaseModel):
    notice: str = Field(min_length=1, max_length=1000)

class FolderCreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)

class FolderPatchModel(FolderCreateModel):
    pass

class PoemPatchModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=20000)
    work_type: str | None = Field(default=None, max_length=32)
    meter_type: str | None = Field(default=None, max_length=64)
    form_name: str | None = Field(default=None, max_length=128)

class PoemRevisionCreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_poem_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    sequence: str | None = Field(default=None, max_length=10000)
    content: str = Field(min_length=1, max_length=20000)
    evaluated_content: str = Field(min_length=1, max_length=20000)
    evaluation: dict
    variant_name: str | None = None

class CollectionCreateModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)

class CollectionPatchModel(CollectionCreateModel):
    pass

class CollectionItemModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    poem_id: str = Field(min_length=1, max_length=128)

class GenerateJobModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    meter_type: str
    form_name: str
    variant_name: str | None = None
    theme: str = ''
    rhyme_dict_name: str = 'Xinyun'
    rhyme_mode: str = 'auto'
    rhyme_parts: dict[str, str] = Field(default_factory=dict)
    requirement: str = ''
    task_type: str = 'instruction'
    use_thinking: bool = False
    cipai_data_path: str = 'PoeTone-main/data/cipai_data.json'
    num_lines: int | None = None
    strict_polyphonic: bool = False
    candidate_count: int = Field(default=1, ge=1, le=20)
    task_options: dict[str, Any] = Field(default_factory=dict)
    sampling: SamplingModel = Field(default_factory=SamplingModel)

    @model_validator(mode='after')
    def use_hanpai_one_shot(self):
        if self.meter_type == '汉俳':
            self.task_type = 'one-shot'
        return self

class RewriteJobModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    original_text: str
    target_line_numbers: list[int] = Field(min_length=1, max_length=64)
    meter_type: str
    form_name: str
    source_poem_id: str | None = None
    source_version: str | None = None
    variant_name: str | None = None
    rhyme_dict_name: str = 'Xinyun'
    rhyme_mode: str = 'auto'
    rhyme_parts: dict[str, str] = Field(default_factory=dict)
    fixed_lines: dict[str, str] = Field(default_factory=dict)
    requirement: str = ''
    theme: str = '局部改写'
    num_lines: int | None = None
    strict_polyphonic: bool = False
    strict_context: bool = True
    candidate_count: int = Field(default=1, ge=1, le=50)
    task_options: dict[str, Any] = Field(default_factory=dict)
    sampling: RewriteSamplingModel = Field(default_factory=lambda: RewriteSamplingModel(max_new_tokens=512))

class PartialGenerateJobModel(RewriteJobModel):
    """统一的部分生成契约；补全场景不要求原诗和目标句号。"""
    original_text: str = ''
    candidate_count: int = Field(default=1, ge=1, le=50)
    sentence_inputs: dict[str, str] | None = None
    target_line_numbers: list[int] = Field(default_factory=list, max_length=64)
    theme: str = ''

    @model_validator(mode='after')
    def check_partial_candidate_count(self):
        if self.candidate_count > 20 and not self.source_poem_id:
            raise ValueError('部分生成的 candidate_count 必须在 1 到 20 之间')
        return self

class WorkerClaimModel(BaseModel):
    worker_id: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    wait_seconds: int = Field(default=15, ge=0, le=20)
    job_kinds: list[str] | None = None
    job_id: str | None = Field(default=None, min_length=1, max_length=64)

class WorkerIdentityModel(BaseModel):
    worker_id: str

class WorkerCompleteModel(WorkerIdentityModel):
    result: dict[str, Any]

class WorkerFailModel(WorkerIdentityModel):
    code: str
    message: str
    retryable: bool = False

class WorkerCandidateModel(WorkerIdentityModel):
    ordinal: int = Field(ge=1)
    status: str | None = None
    attempt: int | None = Field(default=None, ge=0)
    partial_text: str | None = None
    raw_text: str | None = None
    title: str | None = None
    content: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
