"""所有对外 JSON 的严格数据模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Education(BaseModel):
    """一段教育经历；未知字段必须显式为 null。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    school: str | None = None
    major: str | None = None
    degree: str | None = None
    graduation_time: str | None = None


class ResumeInfo(BaseModel):
    """简历信息输出契约。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    @field_validator("skills")
    @classmethod
    def remove_empty_and_duplicate_skills(cls, values: list[str]) -> list[str]:
        """去除空白和重复技能，同时保持原有顺序。"""
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if normalized and normalized.lower() not in seen:
                result.append(normalized)
                seen.add(normalized.lower())
        return result


class ScoreResult(BaseModel):
    """JD 匹配评分输出契约。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    overall_score: int = Field(ge=0, le=100)
    skill_score: int = Field(ge=0, le=100)
    experience_score: int = Field(ge=0, le=100)
    education_score: int = Field(ge=0, le=100)
    comment: str = Field(min_length=1)
    interview_questions: list[str] = Field(default_factory=list)

    @field_validator("interview_questions")
    @classmethod
    def remove_empty_questions(cls, values: list[str]) -> list[str]:
        """移除无效问题。"""
        return [value.strip() for value in values if value and value.strip()]

