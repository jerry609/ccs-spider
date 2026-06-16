"""PaperBot agent exports.

Keep imports lazy so routes that only need a lightweight scholar agent do not
pull in optional report/PDF dependencies during package initialization.
"""

from importlib import import_module

__all__ = [
    "BaseAgent",
    "ResearchAgent",
    "CodeAnalysisAgent",
    "QualityAgent",
    "ConferenceResearchAgent",
    "ReviewerAgent",
    "VerificationAgent",
]

_LAZY_EXPORTS = {
    "BaseAgent": (".base", "BaseAgent"),
    "ResearchAgent": (".research.agent", "ResearchAgent"),
    "CodeAnalysisAgent": (".code_analysis.agent", "CodeAnalysisAgent"),
    "QualityAgent": (".quality.agent", "QualityAgent"),
    "ConferenceResearchAgent": (".conference.agent", "ConferenceResearchAgent"),
    "ReviewerAgent": (".review.agent", "ReviewerAgent"),
    "VerificationAgent": (".verification.agent", "VerificationAgent"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
