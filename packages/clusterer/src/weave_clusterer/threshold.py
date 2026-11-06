"""Baseline clusterer: join the best retrieved cluster when its score clears the threshold. No model calls."""
from weave_core.adapters import AdapterSpec
from weave_core.stages import Stage, UnitKind

from .base import ClustererBase, ClustererSettings, Decision


class ThresholdClusterer(ClustererBase):
    spec = AdapterSpec(name="threshold", version="1", stage=Stage.cluster, input_kind=UnitKind.mention,
                       settings_model=ClustererSettings, capabilities=frozenset(), cacheable=False)

    def decide(self, mention, candidates, cards):
        best = candidates[0]
        if best.score >= self.settings.threshold:
            return Decision("join", best.cluster_id, "above_threshold")
        return Decision("new", None, "below_threshold")
