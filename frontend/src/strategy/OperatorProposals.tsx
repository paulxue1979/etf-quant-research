import type { OperatorChangeSet } from "./types";

const reasonText: Record<string, string> = {
  CHANGESET_STALE: "Draft has changed since Codex prepared this proposal. Ask Codex to prepare a new proposal.",
  CHANGESET_VALIDATION_FAILED: "The proposal no longer passes strategy validation.",
  DRAFT_NOT_EDITABLE: "This strategy is not editable.",
  DRAFT_NOT_FOUND: "The draft is unavailable.",
  APPLIED: "Already applied.",
  REJECTED: "Rejected.",
  STALE: "Draft has changed since this proposal was prepared.",
  CHANGESET_FAILED: "This proposal failed during application. Ask Codex to prepare a new proposal.",
};

interface Props {
  proposals: OperatorChangeSet[];
  draftRevision: number;
  isDirty: boolean;
  busy: boolean;
  error: string | null;
  onRefresh: () => void;
  onApprove: (changeSetId: string) => void;
  onReject: (changeSetId: string) => void;
}

export function OperatorProposals({ proposals, draftRevision, isDirty, busy, error, onRefresh, onApprove, onReject }: Props) {
  const pending = proposals.filter((item) => item.status === "PROPOSED").length;
  return (
    <section className="operator-proposals" aria-label="Codex proposed changes">
      <div className="section-header">
        <div><span className="eyebrow">CODEX PROPOSED CHANGES</span><h2>Draft proposals <span>({pending} pending)</span></h2></div>
        <button className="button button-secondary" type="button" disabled={busy} onClick={onRefresh}>Refresh proposals</button>
      </div>
      {error && <p role="alert" className="operator-error">{error}</p>}
      {proposals.length === 0 && <p>No proposals for this strategy.</p>}
      <div className="operator-proposal-list">
        {proposals.map((proposal) => {
          const canApprove = proposal.approval_gate.can_approve && !isDirty && !busy && draftRevision === proposal.expected_draft_revision;
          const reason = isDirty ? "Save or discard unsaved editor changes first."
            : draftRevision !== proposal.expected_draft_revision ? reasonText.CHANGESET_STALE
              : reasonText[proposal.approval_gate.reason ?? ""];
          return (
            <article className="operator-proposal" key={proposal.change_set_id}>
              <div className="operator-proposal-heading">
                <div><strong>{proposal.human_readable_diff.summary}</strong><small>{proposal.change_set_id}</small></div>
                <span className="status-pill">{proposal.status}</span>
              </div>
              <p className="operator-meta">Prepared {new Date(proposal.created_at).toLocaleString()} · Draft r{proposal.expected_draft_revision} · Validation {proposal.validation_status}</p>
              <ul className="operator-changes">
                {proposal.human_readable_diff.changes.map((change, index) => (
                  <li key={`${proposal.change_set_id}-${index}`}><strong>{change.label}</strong><span>{change.before} → {change.after}</span></li>
                ))}
              </ul>
              <p className="operator-unchanged"><strong>Unchanged:</strong> {proposal.human_readable_diff.unchanged.join(", ")}</p>
              {proposal.decision_audit && <p className="operator-meta">{proposal.status === "APPLIED" ? "Applied" : "Rejected"} {new Date(proposal.decision_audit.decided_at).toLocaleString()} · Draft r{proposal.decision_audit.draft_revision_before} → r{proposal.decision_audit.draft_revision_after}</p>}
              {proposal.status === "FAILED" && <p role="status" className="operator-error"><strong>Application failed.</strong> Draft was not changed. Reason: {proposal.safe_message ?? "The application could not be completed."} Ask Codex to prepare a new proposal.</p>}
              {proposal.status === "PROPOSED" && (
                <div className="operator-actions">
                  {proposal.validation_status === "FAIL" && <p role="status">This proposal currently fails validation. Approval will re-check it and record a failed attempt without changing the Draft.</p>}
                  <button className="button button-primary" type="button" disabled={!canApprove} onClick={() => onApprove(proposal.change_set_id)}>Approve Changes</button>
                  <button className="button button-secondary" type="button" disabled={busy} onClick={() => onReject(proposal.change_set_id)}>Reject</button>
                  {!canApprove && reason && <span role="status">{reason}</span>}
                </div>
              )}
              <details><summary>Advanced proposal details</summary><dl>
                <dt>ChangeSet ID</dt><dd>{proposal.change_set_id}</dd>
                <dt>Expected revision</dt><dd>{proposal.expected_draft_revision}</dd>
                <dt>Before hash</dt><dd>{proposal.before_hash}</dd>
                <dt>Proposed after hash</dt><dd>{proposal.proposed_after_hash}</dd>
                <dt>Operations</dt><dd><pre>{JSON.stringify(proposal.operations, null, 2)}</pre></dd>
              </dl></details>
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function proposalErrorText(code?: string): string {
  if (code === "CHANGESET_NOT_FOUND") return "This proposal no longer exists. Refresh the list.";
  if (code === "ALREADY_APPLIED") return "This proposal has already been applied. Refresh the draft.";
  if (code === "ALREADY_REJECTED") return "This proposal has already been rejected.";
  return reasonText[code ?? ""] ?? "The proposal decision failed. No draft change was applied.";
}
