import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { conditionSummary, ConditionTreeEditor } from "./ConditionTreeEditor";
import { createDefaultEditorState, createDefaultRegimeEditor, createRuleGroup } from "./editor";
import { RegimeStateMachineEditor } from "./RegimeStateMachineEditor";

describe("peak drawdown transition editor", () => {
  afterEach(cleanup);

  it("offers a separate OR entry branch only for VALUE and DEEP_VALUE destinations", () => {
    const group = createRuleGroup(["QQQ"]);
    const onChange = vi.fn();
    const view = render(<ConditionTreeEditor group={group} assets={["QQQ"]} priceField="adjusted_close" issues={[]} onChange={onChange} />);
    expect(screen.queryByRole("button", { name: "+ 距前高回撤" })).toBeNull();
    view.rerender(<ConditionTreeEditor group={group} assets={["QQQ"]} priceField="adjusted_close" issues={[]} allowPeak onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: "+ 距前高回撤" }));
    const updated = onChange.mock.lastCall?.[0];
    expect(updated.operator).toBe("or");
    expect(updated.children).toHaveLength(2);
    expect(updated.children[1].left.type).toBe("peak_drawdown");
    expect(updated.children[1].right.value).toBe("-20");
    expect(conditionSummary(updated)).toContain("OR QQQ 距前高回撤 <= -20%");
  });

  it("does not offer peak drawdown on an exit from DEEP_VALUE back to VALUE", () => {
    const state = createDefaultEditorState();
    const original = createDefaultRegimeEditor(state.assets);
    const model = {
      ...original,
      transitions: original.transitions.map((transition) =>
        transition.fromState === "DEEP_VALUE" ? { ...transition, toState: "VALUE" } : transition,
      ),
    };
    render(<RegimeStateMachineEditor state={state} model={model} issues={[]} onChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /DEEP_VALUE → VALUE.*Priority/ }));
    expect(screen.queryByRole("button", { name: "+ 距前高回撤" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /VALUE → DEEP_VALUE.*Priority/ }));
    expect(screen.getByRole("button", { name: "+ 距前高回撤" })).toBeTruthy();
  });
});
