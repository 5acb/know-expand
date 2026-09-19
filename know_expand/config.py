from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class BibliographyConfig:
    foundational_fraction: float = 0.65
    frontier_months: int = 24
    depth_totals: dict[str, int] = field(default_factory=lambda: {
        "survey": 10, "standard": 30, "deep": 50
    })
    ss_max_retries: int = 5
    ss_retry_initial_delay: float = 30.0
    ss_max_backoff: float = 120.0
    ss_anchors_n: int = 3
    # Junk-venue filter: drop self-upload / preprint-mill results (Zenodo, SSRN,
    # Research Square, TechRxiv) that carry inflated citation counts and crowd
    # peer-reviewed work out of the frontier bucket. arXiv is deliberately not
    # listed. Regexes are matched case-insensitively against the S2 venue name.
    junk_venue_filter: bool = True
    junk_venues: list[str] = field(default_factory=lambda: [
        r"zenodo",
        r"ssrn",
        r"research\s*square",
        r"techrxiv",
    ])
    junk_doi_prefixes: list[str] = field(default_factory=lambda: [
        "10.5281/zenodo",   # Zenodo
        "10.2139/ssrn",     # SSRN
        "10.21203/",        # Research Square
        "10.36227/",        # TechRxiv
    ])


@dataclass
class CentralityConfig:
    structural_weight: float = 2.0
    core_effective_threshold: float = 5.0
    core_chunk_fraction: float = 0.3
    supporting_effective_threshold: float = 2.0
    alias_cosine_threshold: float = 0.92


@dataclass
class RateLimitConfig:
    max_rate: float
    time_period: float


@dataclass
class ConcurrencyConfig:
    default: int = 8


@dataclass
class NlpModelsConfig:
    spacy: str = "en_core_web_sm"
    embedding: str = "BAAI/bge-m3"
    tokenizer: str = "cl100k_base"


@dataclass
class ChunkingConfig:
    word_size: int = 800
    max_tokens: int = 4000
    spacy_char_limit: int = 50000
    llm_map_char_limit: int = 6000


@dataclass
class KeywordExtractionConfig:
    top_n: int = 20
    ngram_min: int = 1
    ngram_max: int = 3


@dataclass
class GraphDefaultsConfig:
    node_tier: str = "journeyman"
    node_xp: int = 100


@dataclass
class CriticThinkingConfig:
    budget_tokens: int = 0


@dataclass
class ResearchContextConfig:
    """Prompt-size controls for S5 persona prompts.

    Depth-aware caps prevent the ~44k-char prompts that caused provider timeouts
    while still giving deep runs more context than shallow ones.
    """
    nodes_top_n: int = 30
    sources_max_terms: dict[str, int] = field(default_factory=lambda: {
        "survey": 5, "standard": 8, "deep": 15
    })
    sources_max_per_term: dict[str, int] = field(default_factory=lambda: {
        "survey": 1, "standard": 1, "deep": 2
    })
    sources_max_chars: dict[str, int] = field(default_factory=lambda: {
        "survey": 300, "standard": 400, "deep": 500
    })


@dataclass
class MemoryConfig:
    provider: str = "qdrant"
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "know_expand_memory"


@dataclass
class Config:
    bibliography: BibliographyConfig
    centrality: CentralityConfig
    rate_limits: dict[str, RateLimitConfig]
    concurrency: ConcurrencyConfig
    timeouts: dict[str, int]
    adversarial_rounds: dict[str, int]
    boilerplate_stop_list: list[str]
    models: dict[str, list[str]]    # role -> ordered model list
    nlp_models: NlpModelsConfig = field(default_factory=NlpModelsConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    keyword_extraction: KeywordExtractionConfig = field(default_factory=KeywordExtractionConfig)
    graph_defaults: GraphDefaultsConfig = field(default_factory=GraphDefaultsConfig)
    research_context: ResearchContextConfig = field(default_factory=ResearchContextConfig)
    critic_thinking: CriticThinkingConfig = field(default_factory=CriticThinkingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)


def load_config(
    config_path: Path | None = None,
    models_path: Path | None = None,
) -> Config:
    if config_path is None:
        c_p = Path("config.yaml")
        if not c_p.exists():
            c_p = Path(__file__).parent / "config.yaml"
    else:
        c_p = config_path

    if models_path is None:
        m_p = Path("models.yaml")
        if not m_p.exists():
            m_p = Path(__file__).parent / "models.yaml"
    else:
        m_p = models_path

    raw = yaml.safe_load(c_p.read_text())
    models_raw = yaml.safe_load(m_p.read_text())

    def _section(key, cls):
        raw_val = raw.get(key, {})
        return cls(**raw_val) if raw_val else cls()

    return Config(
        bibliography=BibliographyConfig(**raw["bibliography"]),
        centrality=CentralityConfig(**raw["centrality"]),
        rate_limits={
            name: RateLimitConfig(**vals)
            for name, vals in raw["rate_limits"].items()
        },
        concurrency=ConcurrencyConfig(**raw["concurrency"]),
        timeouts=raw["timeouts"],
        adversarial_rounds=raw["adversarial_rounds"],
        boilerplate_stop_list=raw["boilerplate_stop_list"],
        models=models_raw["roles"],
        nlp_models=_section("nlp_models", NlpModelsConfig),
        chunking=_section("chunking", ChunkingConfig),
        keyword_extraction=_section("keyword_extraction", KeywordExtractionConfig),
        graph_defaults=_section("graph_defaults", GraphDefaultsConfig),
        research_context=_section("research_context", ResearchContextConfig),
        critic_thinking=_section("critic_thinking", CriticThinkingConfig),
        memory=_section("memory", MemoryConfig),
    )

