"""A user's optional profile: who they are and what they research.

Every field is optional and personal: it is stored only for the signed-in user (as a user setting, so "Delete my
data" removes it), never shown to administrators, and never sent to search providers or the LLM. Choices are fixed
lists so free text cannot smuggle in anything unexpected; free-text fields are short and trimmed.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

PREFER_NOT = "Prefer not to say"
AGE_RANGES = [PREFER_NOT, "Under 18", "18–24", "25–34", "35–44", "45–54", "55–64", "65 or older"]
SEXES = [PREFER_NOT, "Female", "Male", "Intersex", "Another option"]
EDUCATION = [PREFER_NOT, "Secondary school", "Vocational or diploma", "Bachelor's degree", "Master's degree",
             "Doctorate", "Other"]
INDUSTRIES = [PREFER_NOT, "Academia and research", "Technology and software", "Healthcare and life sciences",
              "Finance and banking", "Consulting", "Government and public sector", "Education", "Manufacturing",
              "Energy and environment", "Media and communications", "Retail and consumer", "Legal", "Non-profit",
              "Student", "Other"]
INTEREST_SUGGESTIONS = ["Artificial intelligence", "Climate and energy", "Economics", "Education", "Health and medicine",
                        "Life sciences", "Markets and business strategy", "Policy and regulation", "Security",
                        "Software engineering", "Social sciences", "Technology trends"]
MAX_INTERESTS = 10


class UserProfile(BaseModel):
    display_name: str = Field("", max_length=60)
    age_range: str = PREFER_NOT
    sex: str = PREFER_NOT
    education: str = PREFER_NOT
    industry: str = PREFER_NOT
    role: str = Field("", max_length=80)  # e.g. "Product manager", "PhD student"
    research_interests: list[str] = Field(default_factory=list, max_length=MAX_INTERESTS)

    @field_validator("display_name", "role", mode="before")
    @classmethod
    def _trim(cls, value):
        return " ".join(str(value or "").split())

    @field_validator("age_range", "sex", "education", "industry")
    @classmethod
    def _choice(cls, value, info):
        options = {"age_range": AGE_RANGES, "sex": SEXES, "education": EDUCATION, "industry": INDUSTRIES}[info.field_name]
        if value not in options:
            raise ValueError(f"Choose one of the listed options for {info.field_name.replace('_', ' ')}.")
        return value

    @field_validator("research_interests", mode="before")
    @classmethod
    def _interests(cls, value):
        cleaned = [" ".join(str(v).split())[:40] for v in (value or []) if str(v).strip()]
        return list(dict.fromkeys(cleaned))  # unique, in order
