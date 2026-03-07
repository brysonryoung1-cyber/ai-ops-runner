import { Playbook } from "./playbooks";

export interface RunNextInputs {
  project_id: string;
  approvals_pending: number;
  needs_human: boolean;
  core_status: "PASS" | "FAIL" | "UNKNOWN";
  business_dod_pass?: boolean | null;
  playbooks: Playbook[];
}

export interface RunNextRecommendation {
  playbook_id: string;
  title: string;
  rationale: string;
  expected_outputs: string[];
}

function findPlaybook(playbooks: Playbook[], candidates: string[]): Playbook | null {
  for (const candidate of candidates) {
    const match = playbooks.find((playbook) => playbook.id === candidate);
    if (match) return match;
  }
  return null;
}

function fallbackReviewPlaybook(projectId: string, playbooks: Playbook[]): Playbook | null {
  return (
    playbooks.find((playbook) => playbook.id.startsWith(projectId.split("_")[0]) && playbook.id.endsWith(".review_approvals")) ??
    playbooks.find((playbook) => playbook.kind === "review") ??
    null
  );
}

export function decideRunNext(input: RunNextInputs): RunNextRecommendation | null {
  const prefix = input.project_id === "infra_openclaw"
    ? "infra"
    : input.project_id === "soma_kajabi"
      ? "soma"
      : input.project_id === "pred_markets"
        ? "pred"
        : "orb";

  if (input.approvals_pending > 0) {
    const playbook = findPlaybook(input.playbooks, [`${prefix}.review_approvals`]) ?? fallbackReviewPlaybook(input.project_id, input.playbooks);
    if (playbook) {
      return {
        playbook_id: playbook.id,
        title: playbook.title,
        rationale: "Approvals are already queued; resolve them before launching new work.",
        expected_outputs: ["Approval card context", "Request rationale", "Proof links"],
      };
    }
  }

  if (input.needs_human) {
    const playbook = findPlaybook(input.playbooks, [`${prefix}.reauth_resume`, `${prefix}.run_doctor_heal`]);
    if (playbook) {
      return {
        playbook_id: playbook.id,
        title: playbook.title,
        rationale: "A HUMAN_ONLY gate is open, so the next useful action is to reauthenticate and resume.",
        expected_outputs: ["Human gate instructions", "Resume proof bundle"],
      };
    }
  }

  if (input.core_status === "FAIL") {
    const playbook = findPlaybook(input.playbooks, [`${prefix}.run_doctor_heal`]);
    if (playbook) {
      return {
        playbook_id: playbook.id,
        title: playbook.title,
        rationale: "Core status is degraded, so deterministic doctor/heal should run before anything else.",
        expected_outputs: ["Doctor result", "Remediation artifacts", "Updated proof links"],
      };
    }
  }

  if (input.project_id === "soma_kajabi" && input.business_dod_pass === false) {
    const playbook = findPlaybook(input.playbooks, ["soma.fix_business_dod"]);
    if (playbook) {
      return {
        playbook_id: playbook.id,
        title: playbook.title,
        rationale: "Technical checks passed, but Soma still fails the business DoD contract.",
        expected_outputs: ["Before/after Business DoD checks", "Kajabi fix proof"],
      };
    }
  }

  const playbook = findPlaybook(input.playbooks, [`${prefix}.run_to_done`, `${prefix}.run_health`]);
  if (!playbook) return null;
  return {
    playbook_id: playbook.id,
    title: playbook.title,
    rationale: "No blockers are active, so the recommended next step is the default run-to-done playbook.",
    expected_outputs: playbook.proof_expectations,
  };
}
