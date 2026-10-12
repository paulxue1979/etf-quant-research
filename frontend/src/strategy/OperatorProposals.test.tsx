import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OperatorProposals, proposalErrorText } from "./OperatorProposals";
import type { OperatorChangeSet } from "./types";

const proposal: OperatorChangeSet = {
  change_set_id: "change-set-test",
  strategy_id: "peak-test",
  status: "PROPOSED",
  created_at: "2026-10-12T00:00:00Z",
  updated_at: "2026-10-12T00:00:00Z",
  expected_draft_revision: 2,
  before_hash: "before-hash",
  proposed_after_hash: "after-hash",
  validation_status: "PASS",
  operations: [{ operation_type: "SET_PEAK_DRAWDOWN_THRESHOLD", transition_id: "entry", value: -0.2 }],
  human_readable_diff: {
    summary: "Codex proposes 2 changes",
    changes: [{ label: "VALUE entry drawdown", before: "-18%", after: "-20%" }],
    unchanged: ["Exit logic", "MA period", "Execution timing"],
  },
  approval_gate: { can_approve: true, reason: null },
};

const callbacks = { onRefresh: vi.fn(), onApprove: vi.fn(), onReject: vi.fn() };

function show(item: OperatorChangeSet = proposal, revision = 2, isDirty = false) {
  return render(<OperatorProposals proposals={[item]} draftRevision={revision} isDirty={isDirty} busy={false} error={null} {...callbacks} />);
}

describe("Codex proposals", () => {
  it("renders the semantic diff and permits exact-ID approval or rejection", () => {
    callbacks.onApprove.mockClear();
    callbacks.onReject.mockClear();
    const view = show();
    expect(screen.getByText("Codex proposes 2 changes")).toBeInTheDocument();
    expect(screen.getByText("-18% → -20%")).toBeInTheDocument();
    expect(screen.getByText(/Exit logic, MA period, Execution timing/)).toBeInTheDocument();
    expect(screen.getByText("(1 pending)")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve Changes" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(callbacks.onApprove).toHaveBeenCalledWith("change-set-test");
    expect(callbacks.onReject).toHaveBeenCalledWith("change-set-test");
    view.unmount();
  });

  it("blocks stale, failed validation, dirty editor and server-ineligible proposals", () => {
    const cases: [OperatorChangeSet, number, boolean][] = [
      [{ ...proposal, approval_gate: { can_approve: false, reason: "CHANGESET_STALE" } }, 2, false],
      [{ ...proposal, validation_status: "FAIL", approval_gate: { can_approve: false, reason: "CHANGESET_VALIDATION_FAILED" } }, 2, false],
      [proposal, 2, true],
      [proposal, 3, false],
    ];
    for (const [item, revision, dirty] of cases) {
      const view = show(item, revision, dirty);
      expect(screen.getByRole("button", { name: "Approve Changes" })).toBeDisabled();
      view.unmount();
    }
  });

  it("shows persisted applied/rejected status and useful errors", () => {
    const applied = show({ ...proposal, status: "APPLIED", decision_audit: {
      result: "APPLIED", decided_at: "2026-10-12T01:00:00Z",
      draft_revision_before: 2, draft_revision_after: 3, hash_after: "after-hash",
    } });
    expect(screen.getByText(/Draft r2 → r3/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve Changes" })).not.toBeInTheDocument();
    applied.unmount();
    const rejected = show({ ...proposal, status: "REJECTED" });
    expect(screen.getByText("REJECTED")).toBeInTheDocument();
    rejected.unmount();
    expect(proposalErrorText("CHANGESET_NOT_FOUND")).toMatch(/no longer exists/);
    expect(proposalErrorText("ALREADY_APPLIED")).toMatch(/already been applied/);
  });
});
