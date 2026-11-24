"""基于公开评分和行为证据形成可检验的诊断。"""

from statistics import mean


class FailureAnalyzer:
    def analyze(self, observations):
        findings = []
        for row in observations:
            if row["score"] >= 100:
                continue
            if row["terminal"] in {"turn_limit", "timed_out"}:
                gap = "budget_exhausted"
            elif row["tool_errors"]:
                gap = "tool_errors"
            elif not row["writes"]:
                gap = "no_successful_edit"
            elif not row["public_checks"]:
                gap = "no_successful_public_check"
            else:
                gap = "score_gap_after_public_check"
            findings.append({"gap": gap, **row})
        return sorted(findings, key=lambda f: (f["score"], f["case"], f["run"]))

    def prediction(self, proposal, before, after):
        cases = {r["case"] for r in before if r["session"] in proposal.evidence}
        metric = proposal.metric
        def measure(rows):
            return mean(mean(r[metric] for r in rows if r["case"] == case) for case in sorted(cases))
        old, new = measure(before), measure(after)
        return {"metric": metric, "direction": proposal.direction, "cases": sorted(cases),
                "before": old, "after": new, "supported": new > old if proposal.direction == "increase" else new < old}
