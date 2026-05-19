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
class LlamaCppConfig:
    base_url: str = "http://localhost:8080"
    max_tokens: int = 8192


@dataclass
class ConcurrencyConfig:
    cloud_default: int = 8
    local_default: int = 1


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
class Config:
    bibliography: BibliographyConfig
    centrality: CentralityConfig
    rate_limits: dict[str, RateLimitConfig]
    concurrency: ConcurrencyConfig
    timeouts: dict[str, int]
    adversarial_rounds: dict[str, int]
    boilerplate_stop_list: list[str]
    models: dict[str, list[str]]    # role -> ordered model list
    llamacpp: LlamaCppConfig = field(default_factory=LlamaCppConfig)
    nlp_models: NlpModelsConfig = field(default_factory=NlpModelsConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    keyword_extraction: KeywordExtractionConfig = field(default_factory=KeywordExtractionConfig)
    graph_defaults: GraphDefaultsConfig = field(default_factory=GraphDefaultsConfig)


def load_config(
    config_path: Path = Path("config.yaml"),
    models_path: Path = Path("models.yaml"),
) -> Config:
    raw = yaml.safe_load(config_path.read_text())
    models_raw = yaml.safe_load(models_path.read_text())

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
        llamacpp=_section("llamacpp", LlamaCppConfig),
        nlp_models=_section("nlp_models", NlpModelsConfig),
        chunking=_section("chunking", ChunkingConfig),
        keyword_extraction=_section("keyword_extraction", KeywordExtractionConfig),
        graph_defaults=_section("graph_defaults", GraphDefaultsConfig),
    )
