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


class TaxonomyExplanation(BaseModel):
    body: str                # plain-text explanation for the user


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


# ---------------------------------------------------------------------------
# Stage 3 — Audit
# ---------------------------------------------------------------------------

class CitationRecord(BaseModel):
    id: str                           # CSL-JSON key, e.g. "kwon_2023_vllm"
    type: str = "article-journal"
    title: str
    author: list[dict]                # [{"given": ..., "family": ...}]
    issued: dict                      # {"date-parts": [[year]]}
    DOI: str | None = None
    URL: str | None = None
    abstract: str = ""
    bucket: str                       # foundational | frontier | anchor
    citation_count: int = 0


class GapFinding(BaseModel):
    gap_description: str
    evidence_anchor_ids: list[str]    # which anchor paper IDs support this gap
    defender_argument: str = ""
    finder_rebuttal: str = ""
    verdict: str = "pending"          # real_gap | ambiguous | not_a_gap | pending


class GapAnalysisResult(BaseModel):
    domain_id: str
    gaps: list[GapFinding]


# ---------------------------------------------------------------------------
# Stage 4 — Research
# ---------------------------------------------------------------------------

class CitationUse(BaseModel):
    citation_id: str            # must match a CitationRecord.id from bibliography
    quote_or_claim: str         # the specific claim being cited
    relevance: str              # why this citation supports the claim


class ResearchSection(BaseModel):
    heading: str
    body: str                   # Markdown prose
    citations: list[CitationUse] = Field(default_factory=list)
    confidence: str = "medium"  # high | medium | low


class DomainSummary(BaseModel):
    domain_id: str
    domain_label: str
    overview: str               # 1–2 sentence domain overview
    sections: list[ResearchSection]
    key_open_questions: list[str] = Field(default_factory=list)
    citation_ids_used: list[str] = Field(default_factory=list)


class PersonaOutput(BaseModel):
    """Output from a single persona agent (Theoretician / Engineer / Practitioner)."""
    persona: str                        # "theoretician" | "engineer" | "practitioner"
    sections: list[ResearchSection]
    key_claims: list[str] = Field(default_factory=list)
    citations_used: list[str] = Field(default_factory=list)


class ReconcilerOutput(BaseModel):
    """Output from the reconciler agent that merges all three persona outputs."""
    narrative: str              # full Markdown for the section file
    summary: DomainSummary      # structured summary consumed by Stage 5


class ResearchPlan(BaseModel):
    domain_id: str
    strategy: str               # "top_down" | "bottom_up"
    outline: list[str]          # ordered section headings to pursue
    priority_citation_ids: list[str] = Field(default_factory=list)
    rationale: str = ""


class CritiqueResult(BaseModel):
    domain_id: str
    issues: list[str]           # specific problems found
    suggested_additions: list[str]
    verdict: str                # "accept" | "revise" | "reject"
    revised_summary: DomainSummary | None = None


# ---------------------------------------------------------------------------
# Stage 5 — Synthesize
# ---------------------------------------------------------------------------

class SynthesisInsight(BaseModel):
    insight: str
    domains_involved: list[str]
    evidence: str               # graph edge path or summary field reference
    confidence: str = "medium"


class SynthesisDraft(BaseModel):
    insights: list[SynthesisInsight]
    reading_roadmap: list[str]  # ordered domain sequence for a reader
    boss_nodes: list[str]       # 3-5 key synthesis concepts (new nodes)
    narrative: str              # full Markdown section


class SynthesisCritique(BaseModel):
    trivial_connections: list[str]    # connections that are just prerequisite edges
    unsupported_connections: list[str]
    missing_cross_domain: list[str]
    verdict: str                      # "accept" | "revise"
    revised_narrative: str = ""


class CrossDomainBridge(BaseModel):
    domain_a: str
    domain_b: str
    shared_concept: str          # what connects them
    bridge_text: str             # 1-2 paragraph Markdown explaining the connection
    evidence: str                # which summary field or KG edge supports this


class ConnectorOutput(BaseModel):
    bridges: list[CrossDomainBridge]
    epistemic_stack_note: str    # 1-2 paragraphs describing the overall domain hierarchy


# ---------------------------------------------------------------------------
# Stage 6 — Verify
# ---------------------------------------------------------------------------

class CitationAuditItem(BaseModel):
    section_file: str
    marker: str                 # the [NEEDS_CITATION] text or citation key
    context: str                # surrounding sentence
    status: str                 # "needs_citation" | "verified" | "unknown_key"


class CitationAuditResult(BaseModel):
    items: list[CitationAuditItem]
    total_needs_citation: int
    total_unknown_keys: int
    total_verified: int


# ---------------------------------------------------------------------------
# Stage 7 — Assemble
# ---------------------------------------------------------------------------

class AssemblyManifest(BaseModel):
    domain_order: list[str]
    section_files: list[str]
    bibliography_count: int
    total_words: int
    output_md: str
    output_pdf: str | None = None
