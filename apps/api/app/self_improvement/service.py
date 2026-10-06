from app.self_improvement.engine import analyze, compare_baselines, create_baseline
from app.self_improvement.repository import ImprovementRepository


class ImprovementService:
    def __init__(self, sessions):
        self.repository = ImprovementRepository(sessions)

    def analyze(self, sources, *, repo_sha, configuration_fingerprint, safety_fingerprint):
        if not 1 <= len(sources) <= 64:
            raise ValueError("supply 1..64 evidence sources")
        provenance, observations = [], []
        for source in sources:
            record, items = source.collect()
            if record.source_type == "ci_run" and record.repo_sha != repo_sha:
                raise ValueError(
                    "CI evidence head differs from the requested baseline repository SHA"
                )
            provenance.append(record)
            observations.extend(items)
            if len(observations) > 4096:
                raise ValueError("evidence exceeds observation bound")
        baseline = create_baseline(
            repo_sha=repo_sha,
            configuration_fingerprint=configuration_fingerprint,
            safety_fingerprint=safety_fingerprint,
            sources=provenance,
            observations=observations,
        )
        return self.repository.save_analysis(analyze(baseline))

    def compare(self, before_id, after_id, proposal_id, attestation):
        before = self.repository.analysis(before_id)
        after = self.repository.analysis(after_id)
        proposal = next((p for p in before.proposals if p.id == proposal_id), None)
        if proposal is None:
            raise ValueError("unknown proposal for baseline")
        result = compare_baselines(before.baseline, after.baseline, proposal, attestation)
        return self.repository.save_comparison(result, attestation)
