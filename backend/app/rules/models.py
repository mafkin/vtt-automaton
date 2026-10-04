from typing import Literal

from pydantic import BaseModel, Field


class RuleEntry(BaseModel):
    """One row of the local rules database (built from Archives of Nethys)."""

    id: str
    category: str
    name: str
    aon_url: str
    traits: list[str] = Field(default_factory=list)
    text: str
    source: str | None = None


class RuleRef(BaseModel):
    """A rules entry cited by a ruling, with the verbatim passage that was quoted."""

    entry_id: str
    category: str
    name: str
    aon_url: str
    quote: str
    # Filled from the Foundry UUID index, never by the LLM.
    foundry_uuid: str | None = None


class Ruling(BaseModel):
    query: str
    raw: list[RuleRef]
    interpretation: str
    confidence: Literal["high", "medium", "low"]
    # True when the LLM output failed validation and only retrieved RAW text is returned.
    raw_only: bool = False


class RulingContext(BaseModel):
    """Optional game state sent by the Foundry module (phase 6 fills this in)."""

    actor: str | None = None
    targets: list[str] = Field(default_factory=list)
    notes: str | None = None


class RulingRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["public", "gm"] = "public"
    user: str | None = None
    context: RulingContext | None = None
    render: Literal["none", "foundry", "discord"] = "none"
