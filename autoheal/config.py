from dataclasses import dataclass
import yaml

@dataclass
class LLMConfig:
    provider: str
    openai_api_key: str
    anthropic_api_key: str
    model: str
    temperature: float

@dataclass
class VectorDBConfig:
    provider: str
    api_key: str
    index_name: str
    base_path: str

@dataclass
class VCSConfig:
    provider: str
    github_token: str
    repo: str

@dataclass
class CIConfig:
    provider: str

@dataclass
class ArtifactStoreConfig:
    path: str

@dataclass
class PolicyConfig:
    file: str

@dataclass
class LoggingConfig:
    level: str
    patch_ledger: str

@dataclass
class Config:
    llm: LLMConfig
    vectordb: VectorDBConfig
    vcs: VCSConfig
    ci: CIConfig
    artifact_store: ArtifactStoreConfig
    policy: PolicyConfig
    logging: LoggingConfig

def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return Config(
        llm=LLMConfig(**cfg["llm"]),
        vectordb=VectorDBConfig(**cfg["vectordb"]),
        vcs=VCSConfig(**cfg["vcs"]),
        ci=CIConfig(**cfg["ci"]),
        artifact_store=ArtifactStoreConfig(**cfg["artifact_store"]),
        policy=PolicyConfig(**cfg["policy"]),
        logging=LoggingConfig(**cfg["logging"]),
    )
