from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage 1 — Extract
# ---------------------------------------------------------------------------

class TermOccurrence(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    co_occurring_terms: list[str] = Field(default_factory=list)
    context_snippet: str = ""
    occurrence_count: int = 1


class TermInventory(BaseModel):
    chunk_id: str
    terms: list[TermOccurrence]


class CanonicalTerm(BaseModel):
    name: str
    aliases: list[str]
    centrality: str          # core | supporting | incidental
    occurrence_count: int
    in_structural_zones: bool


# ---------------------------------------------------------------------------
# Stage 2 — Graph
# ---------------------------------------------------------------------------

class DomainProposal(BaseModel):
    id: str                  # snake_case identifier
    label: str               # human-readable name
    definition: str
    example_terms: list[str]
    openalex_concept_id: str | None = None
    openalex_level: int | None = None    # L0–L5; L0-L2 = lumper, L3-L5 = splitter


class TaxonomyProposal(BaseModel):
    strategy: str            # "lumper" or "splitter"
    domains: list[DomainProposal]
    rationale: str


class TermClassification(BaseModel):
    term_name: str
    domain_id: str
    confidence: str          # high | medium | low


class GraphNode(BaseModel):
    id: str
    name: str
    domain: str
    tier: str                # apprentice | journeyman | expert | master
    xp: int
    prerequisites: list[str] = Field(default_factory=list)
    unlocks: list[str] = Field(default_factory=list)
    from_source_doc: bool = False
    centrality: str          # core | supporting | incidental


class GraphEdge(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    type: str                # enables | prerequisite | related


class KnowledgeGraph(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    domains: list[DomainProposal]

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Stage 0.5 — User profile
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    familiarity_level: str       # novice | aware | practitioner | expert
    background_field: str
    q3_correct: bool
    math_comfort: str            # intuition_only | skim | engage | formal
    learning_goal: str           # explain | critique | apply | research
    q6_response: str
    q6_known: bool | str         # True | False | "partial"
    q7_correct: bool | str
    known_concepts: list[str]
    unknown_concepts: list[str]
    effective_depth: str         # survey | standard | deep
    math_mode: str               # intuition | equations_explained | full_derivations
    reading_goal_note: str
